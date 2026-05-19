from __future__ import annotations

import asyncio
from typing import Optional

from adapters.backend_client import BackendClient
from services.audio.audio_service import AudioService
from services.wake_word.wake_word_matcher import WakeMatch, WakeWordMatcher
from utils.logger import get_logger

logger = get_logger(__name__)


class WakeWordService:
    """Passive listening loop: record short chunks, STT them, match the keyword."""

    def __init__(
        self,
        audio: AudioService,
        backend: BackendClient,
        matcher: WakeWordMatcher,
        *,
        chunk_seconds: float = 2.0,
        cooldown_seconds: float = 0.1,
    ) -> None:
        self._audio = audio
        self._backend = backend
        self._matcher = matcher
        self._chunk_seconds = max(1.0, chunk_seconds)
        self._cooldown = max(0.0, cooldown_seconds)
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    def reset(self) -> None:
        self._stopped = asyncio.Event()

    async def wait_for_wake(self, stop_event: Optional[asyncio.Event] = None) -> Optional[WakeMatch]:
        """Loop until the wake word is heard or the stop event is set.

        Returns the WakeMatch on detection, or None when stopped before any hit.
        """
        external_stop = stop_event
        while True:
            if external_stop is not None and external_stop.is_set():
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
                logger.info("Wake word detected (keyword=%s) — remainder=%r", match.keyword, match.remainder)
                return match

            if self._cooldown:
                await asyncio.sleep(self._cooldown)
