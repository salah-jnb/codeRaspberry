"""WebSocket client that streams raw PCM to the backend's wake-word endpoint
and notifies when Azure detects the wake word.

Pi connects to ``ws://<backend>/api/ws/wake-word/<robot_id>``, sends a JSON
config (language + keyword variants), then forwards audio chunks. Events
flow back as JSON; we expose them through an asyncio.Queue and a one-shot
``wake_detected`` event so callers can race Azure against Vosk.

This client uses the ``websockets`` library (already pulled by httpx WS in
recent versions; fallback installs ``websockets``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class StreamingWakeMatch:
    keyword: str
    transcript: str
    latency_ms: int
    source: str  # "azure_partial" | "azure_final"


def _http_to_ws(http_base: str, path: str) -> str:
    parts = urlsplit(http_base)
    scheme = "wss" if parts.scheme == "https" else "ws"
    cleaned_base = (parts.netloc or parts.path).rstrip("/")
    new_path = "/" + path.lstrip("/")
    return urlunsplit((scheme, cleaned_base, new_path, "", ""))


class AzureStreamingWakeWordClient:
    """Single-shot Azure streaming session — open, stream, detect, close.

    Lifecycle:
      1. ``await session.start(audio_iter)`` — connects, sends config, spawns
         a writer task that pulls PCM chunks from ``audio_iter`` and forwards
         them, plus a reader task that watches for ``wake_detected``.
      2. ``await session.wait_for_wake(timeout)`` — returns the match or None.
      3. ``await session.aclose()`` — tears everything down (WS, tasks).

    The audio iterator is normally fed by RespeakerAdapter's broadcast queue.
    """

    def __init__(
        self,
        backend_base_url: str,
        robot_id: str,
        language: str,
        keywords: List[str],
        *,
        path: str = "/api/ws/wake-word",
        connect_timeout_s: float = 5.0,
    ) -> None:
        self._url = f"{_http_to_ws(backend_base_url, path)}/{robot_id}"
        self._language = language
        self._keywords = list(keywords)
        self._connect_timeout = connect_timeout_s
        self._ws = None  # set in start()
        self._match: Optional[StreamingWakeMatch] = None
        self._wake_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._reader_task: Optional[asyncio.Task] = None
        self._writer_task: Optional[asyncio.Task] = None
        self._closed = False

    @property
    def matched(self) -> Optional[StreamingWakeMatch]:
        return self._match

    async def start(self, audio_iter) -> None:
        """Open the WS and start the reader + writer tasks. ``audio_iter`` is
        an async iterator yielding ``bytes`` PCM chunks."""
        import websockets  # local import — lib is optional, only when used

        logger.info("Wake-word stream: connecting to %s", self._url)
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self._url, max_size=None),
                timeout=self._connect_timeout,
            )
        except Exception:
            logger.exception("Wake-word stream: WS connect failed")
            raise

        config = {"language": self._language, "keywords": self._keywords}
        await self._ws.send(json.dumps(config, ensure_ascii=False))

        self._reader_task = asyncio.create_task(self._reader_loop(), name="azure_ws_reader")
        self._writer_task = asyncio.create_task(self._writer_loop(audio_iter), name="azure_ws_writer")
        # Wait for the server to confirm Azure is ready before returning;
        # this avoids the first few chunks being dropped during Azure handshake.
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=self._connect_timeout)
        except asyncio.TimeoutError:
            logger.warning("Wake-word stream: backend never sent 'ready' — continuing anyway")

    async def wait_for_wake(self, timeout_s: Optional[float]) -> Optional[StreamingWakeMatch]:
        try:
            if timeout_s is None:
                await self._wake_event.wait()
            else:
                await asyncio.wait_for(self._wake_event.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None
        return self._match

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in (self._reader_task, self._writer_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        if self._ws is not None:
            with contextlib.suppress(BaseException):
                await self._ws.close()
        self._ws = None

    async def _reader_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue  # backend only sends text events
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("Wake-word stream: non-JSON frame: %r", raw[:120])
                    continue
                event = payload.get("event")
                if event == "ready":
                    self._ready_event.set()
                elif event == "wake_detected":
                    self._match = StreamingWakeMatch(
                        keyword=str(payload.get("keyword") or ""),
                        transcript=str(payload.get("transcript") or ""),
                        latency_ms=int(payload.get("latency_ms") or 0),
                        source=str(payload.get("source") or "azure"),
                    )
                    logger.info(
                        "🎯 Azure wake match: keyword=%r in %r (%dms, %s)",
                        self._match.keyword, self._match.transcript[:80],
                        self._match.latency_ms, self._match.source,
                    )
                    self._wake_event.set()
                    return
                elif event == "error":
                    logger.warning("Wake-word stream backend error: %s", payload.get("message"))
                elif event == "partial":
                    logger.debug("Azure partial: %r", payload.get("text", "")[:80])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Wake-word stream reader crashed")

    async def _writer_loop(self, audio_iter) -> None:
        assert self._ws is not None
        try:
            async for chunk in audio_iter:
                if not chunk:
                    continue
                if self._wake_event.is_set() or self._closed:
                    return
                await self._ws.send(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Wake-word stream writer crashed")
