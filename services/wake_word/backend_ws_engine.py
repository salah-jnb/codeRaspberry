"""Direct backend WebSocket wake-word engine.

This engine mirrors ``tests/test_ws_wake_word.py respeaker``: it opens the
backend WebSocket immediately, sends the Azure language + keyword variants, and
streams raw S16_LE mono PCM from the ReSpeaker until the backend emits
``wake_detected``.
"""

from __future__ import annotations

import asyncio
from typing import Iterable, Optional

from adapters.respeaker_adapter import RespeakerAdapter
from services.wake_word.azure_streaming_client import AzureStreamingWakeWordClient
from services.wake_word.wake_word_matcher import WakeMatch, WakeWordMatcher
from utils.logger import get_logger

logger = get_logger(__name__)


class BackendWsWakeWordEngine:
    """Wake-word detector that delegates recognition to the backend WS."""

    def __init__(
        self,
        respeaker: RespeakerAdapter,
        matcher: WakeWordMatcher,
        *,
        language: str,
        keywords: Iterable[str],
        backend_base_url: str,
        robot_id: str,
        chunk_bytes: int = 8000,
        log_partials: bool = True,
    ) -> None:
        self._respeaker = respeaker
        self._matcher = matcher
        self._language = self._to_azure_language(language)
        self._keywords = [kw for kw in keywords if kw and kw.strip()]
        self._backend_base_url = backend_base_url
        self._robot_id = robot_id
        self._chunk_bytes = chunk_bytes
        self._log_partials = log_partials

    @staticmethod
    def _to_azure_language(language: str) -> str:
        value = (language or "").strip()
        if "-" in value:
            return value
        return {
            "ar": "ar-SA",
            "en": "en-US",
            "fr": "fr-FR",
            "es": "es-ES",
        }.get(value.lower(), value or "ar-SA")

    async def prepare(self) -> None:
        # The backend owns Azure recognizer startup; nothing local to preload.
        return None

    async def wait_for_wake(self, stop_event: Optional[asyncio.Event] = None) -> Optional[WakeMatch]:
        logger.info(
            "Backend WS wake-word listening (language=%s, keywords=%d variants, chunk=%d bytes)",
            self._language,
            len(self._keywords),
            self._chunk_bytes,
        )

        client = AzureStreamingWakeWordClient(
            backend_base_url=self._backend_base_url,
            robot_id=self._robot_id,
            language=self._language,
            keywords=self._keywords,
            log_partials=self._log_partials,
        )
        wake_task: Optional[asyncio.Task] = None
        stop_task: Optional[asyncio.Task] = None
        try:
            await client.start(self._respeaker.stream_pcm(self._chunk_bytes))
            wake_task = asyncio.create_task(client.wait_for_wake(None), name="backend-ws-wake")

            if stop_event is None:
                match = await wake_task
            else:
                stop_task = asyncio.create_task(stop_event.wait(), name="backend-ws-stop")
                done, pending = await asyncio.wait(
                    {wake_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if stop_task in done:
                    return None
                match = wake_task.result()

            if match is None:
                return None

            logger.info(
                "Wake word detected: keyword=%r transcript=%r latency=%dms source=%s",
                match.keyword,
                match.transcript[:120],
                match.latency_ms,
                match.source,
            )
            return WakeMatch(
                matched=True,
                keyword=match.keyword,
                raw_text=match.transcript,
                normalized_text=self._matcher.match(match.transcript).normalized_text,
                remainder="",
            )
        finally:
            if wake_task is not None and not wake_task.done():
                wake_task.cancel()
            if stop_task is not None and not stop_task.done():
                stop_task.cancel()
            await client.aclose()
