from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)


class RespeakerAdapter:
    """Capture audio from the ReSpeaker 4-mic USB array via `arecord` (ALSA)."""

    def __init__(
        self,
        alsa_device: str = "plughw:3,0",
        sample_rate: int = 16000,
        channels: int = 1,
        sample_format: str = "S16_LE",
    ) -> None:
        self._device = alsa_device
        self._sample_rate = sample_rate
        self._channels = channels
        self._sample_format = sample_format
        self._lock = asyncio.Lock()
        self._validate_tool()

    @staticmethod
    def _validate_tool() -> None:
        if not shutil.which("arecord"):
            logger.warning("arecord binary not found; recording will fail at runtime")

    async def record(self, duration_seconds: float) -> bytes:
        """Capture `duration_seconds` of WAV audio and return its raw bytes."""
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")

        async with self._lock:
            tmp = Path(tempfile.mkstemp(suffix=".wav", prefix="koda_in_")[1])
            cmd = [
                "arecord",
                "-D", self._device,
                "-d", str(max(1, int(round(duration_seconds)))),
                "-f", self._sample_format,
                "-r", str(self._sample_rate),
                "-c", str(self._channels),
                "-t", "wav",
                "-q",
                str(tmp),
            ]
            logger.debug("arecord %s", " ".join(cmd[1:]))
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    err = (stderr or b"").decode("utf-8", errors="replace").strip()
                    raise RuntimeError(f"arecord failed (code {proc.returncode}): {err}")
                data = tmp.read_bytes()
                if not data:
                    raise RuntimeError("arecord produced an empty file")
                return data
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
