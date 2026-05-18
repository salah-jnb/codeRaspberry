from __future__ import annotations

from typing import Optional

from adapters.respeaker_adapter import RespeakerAdapter
from utils.logger import get_logger

logger = get_logger(__name__)


class AudioService:
    """High-level capture orchestration on top of the ReSpeaker adapter."""

    def __init__(self, adapter: RespeakerAdapter, default_duration_seconds: float = 5.0) -> None:
        self._adapter = adapter
        self._default_duration = default_duration_seconds

    async def record(self, duration_seconds: Optional[float] = None) -> bytes:
        duration = duration_seconds if duration_seconds is not None else self._default_duration
        logger.info("Recording %.1fs of audio", duration)
        wav = await self._adapter.record(duration)
        logger.debug("Captured %d bytes of WAV", len(wav))
        return wav
