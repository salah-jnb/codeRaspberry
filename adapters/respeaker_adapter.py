from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import AsyncIterator, Optional

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

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def channels(self) -> int:
        return self._channels

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

    async def stream_pcm(self, chunk_bytes: int) -> AsyncIterator[bytes]:
        """Yield raw S16_LE PCM chunks from a long-running arecord process.

        The process is opened once and kept running until the consumer cancels
        the generator. This avoids the open/close-per-chunk pattern that
        triggers the ReSpeaker USB firmware disconnect at 16 kHz.

        ``chunk_bytes`` controls the read granularity (250 ms @ 16 kHz mono S16
        is 8000 bytes — a good default for a streaming recognizer).
        """
        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be positive")

        await self._lock.acquire()
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            cmd = [
                "arecord",
                "-D", self._device,
                "-f", self._sample_format,
                "-r", str(self._sample_rate),
                "-c", str(self._channels),
                "-t", "raw",
                "-q",
            ]
            logger.debug("arecord (stream) %s", " ".join(cmd[1:]))

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert proc.stdout is not None
            while True:
                try:
                    data = await proc.stdout.readexactly(chunk_bytes)
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        yield bytes(exc.partial)
                    rc = await proc.wait()
                    stderr = (await proc.stderr.read()).decode("utf-8", errors="replace") if proc.stderr else ""
                    raise RuntimeError(
                        f"arecord stream ended unexpectedly (code {rc}): {stderr.strip()}"
                    ) from exc
                if not data:
                    break
                yield data
        finally:
            if proc is not None and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "arecord refused to die after SIGKILL — leaking subprocess pid=%s",
                            proc.pid,
                        )
            try:
                self._lock.release()
            except RuntimeError:
                pass
