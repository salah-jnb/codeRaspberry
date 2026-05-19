from __future__ import annotations

import asyncio
from typing import Optional

from adapters.backend_client import BackendClient
from services.audio.audio_service import AudioService
from services.wake_word.wake_word_matcher import WakeMatch, WakeWordMatcher
from utils.logger import get_logger

logger = get_logger(__name__)


class WakeWordService:
    """Passive wake-word listener.

    Has two operating modes:

    * **Streaming engine** (preferred) — a :class:`VoskWakeWordEngine` (or any
      object exposing ``async wait_for_wake(stop_event)``) consumes a continuous
      mic stream locally. Latency <300 ms and no Azure calls until the wake
      word fires.
    * **Legacy chunk fallback** — when no engine is injected, fall back to the
      original "record 2 s → POST to Azure STT → string match" loop. Kept so
      the Pi can still boot when Vosk is unavailable, but it is markedly less
      reliable (chunk boundaries can split the keyword; see ``main.py`` log
      "Wake-word chunk also contained …").
    """

    def __init__(
        self,
        audio: AudioService,
        backend: BackendClient,
        matcher: WakeWordMatcher,
        *,
        chunk_seconds: float = 2.0,
        cooldown_seconds: float = 0.1,
        engine=None,
    ) -> None:
        self._audio = audio
        self._backend = backend
        self._matcher = matcher
        self._chunk_seconds = max(1.0, chunk_seconds)
        self._cooldown = max(0.0, cooldown_seconds)
        self._engine = engine
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    def reset(self) -> None:
        self._stopped = asyncio.Event()

    async def prepare(self) -> None:
        if self._engine is not None and hasattr(self._engine, "prepare"):
            await self._engine.prepare()

    async def wait_for_wake(self, stop_event: Optional[asyncio.Event] = None) -> Optional[WakeMatch]:
        if self._engine is not None:
            return await self._engine.wait_for_wake(stop_event)
        return await self._wait_for_wake_legacy(stop_event)

    async def _wait_for_wake_legacy(self, stop_event: Optional[asyncio.Event]) -> Optional[WakeMatch]:
        """Original chunked Azure-STT path; only used when no engine is injected."""
        while True:
            if stop_event is not None and stop_event.is_set():
                return None
            if self._stopped.is_set():
                return None

            try:
                wav = await self._audio.record(self._chunk_seconds)
            except Exception as exc:
                logger.warning("Wake-word chunk recording failed: %s — retrying", exc)
                await asyncio.sleep(1.0)
                continue

            try:
                text = await self._backend.speech_to_text(wav)
            except Exception as exc:
                logger.warning("Wake-word STT failed: %s — retrying", exc)
                await asyncio.sleep(1.0)
                continue

            if not text:
                continue

            logger.info("Heard: %r", text[:120])
            match = self._matcher.match(text)
            if match.matched:
                logger.info("Wake word detected (keyword=%s) — remainder=%r",
                            match.keyword, match.remainder)
                return match

            if self._cooldown:
                await asyncio.sleep(self._cooldown)
