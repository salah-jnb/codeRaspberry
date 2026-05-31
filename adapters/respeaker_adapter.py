from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import AsyncIterator, List, Optional

from utils.logger import get_logger
from utils.subprocess_registry import track_subprocess, untrack_subprocess

logger = get_logger(__name__)


class RespeakerAdapter:
    """Capture audio from the ReSpeaker 4-Mic USB array.

    The ReSpeaker v2.0 firmware exposes **6 channels** by default:
      - ch0 = AEC + Beamforming + Noise Suppression + AGC (processed mono)
      - ch1..4 = raw mic inputs (no processing)
      - ch5 = playback reference (for AEC)

    Asking ALSA for `-c 1` triggers an automatic downmix that averages all 6
    channels together, **diluting the clean signal with raw noise**. We instead
    capture the native channel count and use sox's `remix` effect to keep
    only the processed channel before forwarding mono PCM to Vosk / Azure STT.

    Tooling:
      - `sox` is used when `native_channels > 1` (clean path).
      - `arecord` is used when `native_channels == 1` (no remix needed, less deps).
    """

    def __init__(
        self,
        alsa_device: str = "plughw:3,0",
        sample_rate: int = 16000,
        channels: int = 1,
        sample_format: str = "S16_LE",
        native_channels: int = 6,
        processed_channel_index: int = 0,
    ) -> None:
        self._device = alsa_device
        self._sample_rate = sample_rate
        self._channels = max(1, int(channels))  # output channels (always 1 in practice)
        self._sample_format = sample_format
        self._native_channels = max(1, int(native_channels))
        self._processed_channel_index = max(0, int(processed_channel_index))
        self._lock = asyncio.Lock()
        self._needs_remix = self._native_channels > 1
        self._validate_tools()

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def native_channels(self) -> int:
        return self._native_channels

    def _validate_tools(self) -> None:
        if self._needs_remix:
            if not shutil.which("sox"):
                logger.warning(
                    "sox not found but native_channels=%d > 1. The ReSpeaker DSP-cleaned "
                    "channel cannot be extracted — falling back to raw arecord (lower quality). "
                    "Install with: sudo apt install sox",
                    self._native_channels,
                )
                self._needs_remix = False
        else:
            if not shutil.which("arecord"):
                logger.warning("arecord binary not found; recording will fail at runtime")

    def _bit_depth(self) -> int:
        # S16_LE → 16 bits; S24_LE → 24 bits; etc.
        digits = "".join(c for c in self._sample_format if c.isdigit())
        return int(digits) if digits else 16

    def _sox_capture_cmd(self, output_type: str, output_target: str, duration_s: Optional[float] = None) -> List[str]:
        """Build a sox command that grabs `native_channels` and remixes to mono ch0.

        `output_type` is sox's `-t` flag for output (e.g. "raw", "wav").
        `output_target` is a file path or "-" for stdout.

        CRITICAL: explicit output-side ``-c 1`` is required. On the Pi OS sox
        build, omitting it causes ``remix`` to be silently ignored on
        ``-t raw -`` output — sox dumps all N native channels interleaved and
        the consumer (Vosk / Azure WS) reads garbage at N× real-time speed.
        Validated on 2026-05-30: without ``-c 1`` on output, 234 chunks of
        "audio" in 10s instead of 40, no STT match. With ``-c 1`` on output:
        40 chunks in 10s, Azure matches "محسن" in 2.6 s. See
        ``tests/test_ws_wake_word.py`` for the diagnostic.
        """
        bit_depth = str(self._bit_depth())
        cmd: List[str] = [
            "sox", "-q",
            # Input side
            "-t", "alsa", self._device,
            "-r", str(self._sample_rate),
            "-c", str(self._native_channels),
            "-b", bit_depth,
            "-e", "signed-integer",
            # Output side — MUST repeat -c/-r/-b/-e or sox keeps the input's
            # 6 channels in the output stream and `remix` becomes a no-op.
            "-c", "1",
            "-r", str(self._sample_rate),
            "-b", bit_depth,
            "-e", "signed-integer",
            "-t", output_type,
        ]
        if output_type == "raw":
            cmd.append("-L")  # little-endian for raw output
        cmd.append(output_target)
        # `remix N` keeps ONLY input channel N (1-indexed in sox) → mono output.
        cmd += ["remix", str(self._processed_channel_index + 1)]
        if duration_s is not None and duration_s > 0:
            cmd += ["trim", "0", f"{duration_s:.3f}"]
        return cmd

    def _arecord_capture_cmd(self, output_type: str, output_target: str, duration_s: Optional[float] = None) -> List[str]:
        cmd: List[str] = [
            "arecord",
            "-D", self._device,
            "-f", self._sample_format,
            "-r", str(self._sample_rate),
            "-c", str(self._channels),
            "-t", output_type,
            "-q",
        ]
        if duration_s is not None and duration_s > 0:
            cmd += ["-d", str(max(1, int(round(duration_s))))]
        cmd.append(output_target)
        return cmd

    def _build_record_cmd(self, output_path: str, duration_s: float) -> List[str]:
        if self._needs_remix:
            return self._sox_capture_cmd("wav", output_path, duration_s=duration_s)
        return self._arecord_capture_cmd("wav", output_path, duration_s=duration_s)

    def _build_stream_cmd(self) -> List[str]:
        # "-" = stdout. Consumer reads raw PCM frames continuously.
        if self._needs_remix:
            return self._sox_capture_cmd("raw", "-")
        return self._arecord_capture_cmd("raw", "-")

    async def record(self, duration_seconds: float) -> bytes:
        """Capture `duration_seconds` of WAV audio and return its raw bytes."""
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")

        async with self._lock:
            tmp = Path(tempfile.mkstemp(suffix=".wav", prefix="koda_in_")[1])
            cmd = self._build_record_cmd(str(tmp), duration_seconds)
            logger.debug("%s %s", cmd[0], " ".join(cmd[1:]))
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    err = (stderr or b"").decode("utf-8", errors="replace").strip()
                    raise RuntimeError(f"{cmd[0]} failed (code {proc.returncode}): {err}")
                data = tmp.read_bytes()
                if not data:
                    raise RuntimeError(f"{cmd[0]} produced an empty file")
                return data
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    async def stream_pcm(self, chunk_bytes: int) -> AsyncIterator[bytes]:
        """Yield raw S16_LE mono PCM chunks from a long-running capture process.

        The process is opened once and kept running until the consumer cancels
        the generator. `chunk_bytes` controls the read granularity (250 ms at
        16 kHz mono S16 = 8000 bytes — a good default for streaming recognisers).
        """
        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be positive")

        await self._lock.acquire()
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            cmd = self._build_stream_cmd()
            logger.debug("%s (stream) %s", cmd[0], " ".join(cmd[1:]))
            logger.info(
                "Mic capture: %s (native=%d → mono ch%d, denoise=%s)",
                cmd[0], self._native_channels, self._processed_channel_index,
                "ON" if self._needs_remix else "OFF (single-channel firmware)",
            )

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            track_subprocess(proc, label=f"respeaker.stream_pcm({cmd[0]})")
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
                        f"{cmd[0]} stream ended unexpectedly (code {rc}): {stderr.strip()}"
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
                            "%s refused to die after SIGKILL — leaking subprocess pid=%s",
                            "capture", proc.pid,
                        )
            untrack_subprocess(proc)
            try:
                self._lock.release()
            except RuntimeError:
                pass
