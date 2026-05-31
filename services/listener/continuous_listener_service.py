from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from adapters.respeaker_adapter import RespeakerAdapter
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ListenerConfig:
    max_seconds: float = 15.0
    silence_duration_s: float = 1.5
    silence_threshold_pct: float = 1.0
    start_threshold_pct: float = 1.0
    min_speech_seconds: float = 0.2
    initial_silence_max_s: float = 5.0


class ContinuousListenerService:
    """Record audio until N seconds of silence are detected (VAD via sox).

    Falls back to a fixed-duration capture if sox is not installed.
    """

    def __init__(
        self,
        adapter: RespeakerAdapter,
        config: ListenerConfig | None = None,
    ) -> None:
        self._adapter = adapter
        self._config = config or ListenerConfig()
        self._sox_available = shutil.which("sox") is not None
        if not self._sox_available:
            logger.warning("sox not found — VAD disabled, using fixed %.1fs recording", self._config.max_seconds)

    async def listen(self) -> bytes:
        if self._sox_available:
            try:
                return await self._record_with_vad()
            except Exception:
                logger.exception("sox VAD recording failed; falling back to fixed-duration capture")
        return await self._adapter.record(self._config.max_seconds)

    async def _record_with_vad(self) -> bytes:
        out_path = Path(tempfile.mkstemp(suffix=".wav", prefix="koda_listen_")[1])
        cfg = self._config
        native_ch = self._adapter_native_channels()
        processed_idx = self._adapter_processed_channel()
        # Capture the firmware's native channel count, then `remix N` keeps ONLY
        # the DSP-processed channel (ch0 by default). CRITICAL: on Pi OS sox,
        # `remix` is silently ignored unless the OUTPUT side also declares
        # `-c 1` — without it, all native channels are kept in the WAV and the
        # backend Azure STT receives multi-channel garbage. See
        # memory/project_sox_remix_raw_bug.md.
        sr = str(self._adapter_sample_rate())
        cmd = [
            "sox", "-q",
            # Input side
            "-t", "alsa", self._adapter_device(),
            "-r", sr,
            "-c", str(native_ch),
            "-b", "16",
        ]
        if native_ch > 1:
            # Re-declare format on output side to force sox to actually apply remix.
            cmd += ["-c", "1", "-r", sr, "-b", "16", "-e", "signed-integer"]
        cmd += ["-t", "wav", str(out_path)]
        if native_ch > 1:
            cmd += ["remix", str(processed_idx + 1)]  # sox is 1-indexed
        cmd += [
            "silence",
            "1", f"{cfg.min_speech_seconds:.2f}", f"{cfg.start_threshold_pct}%",
            "1", f"{cfg.silence_duration_s:.2f}", f"{cfg.silence_threshold_pct}%",
        ]
        logger.debug("sox %s", " ".join(cmd[1:]))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=cfg.max_seconds + cfg.initial_silence_max_s,
                )
            except asyncio.TimeoutError:
                logger.warning("sox exceeded max recording time; terminating")
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                stderr = b""

            if proc.returncode not in (0, None):
                err = (stderr or b"").decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"sox exited with {proc.returncode}: {err}")

            data = out_path.read_bytes()
            if not data:
                raise RuntimeError("sox produced an empty WAV")
            return data
        finally:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _adapter_device(self) -> str:
        return getattr(self._adapter, "_device")

    def _adapter_sample_rate(self) -> int:
        return getattr(self._adapter, "_sample_rate")

    def _adapter_channels(self) -> int:
        return getattr(self._adapter, "_channels")

    def _adapter_native_channels(self) -> int:
        """Channels the ReSpeaker firmware actually exposes (6 by default).
        Falls back to output `_channels` for adapters that don't track this
        separately (e.g. non-ReSpeaker mics)."""
        return getattr(self._adapter, "_native_channels", self._adapter_channels())

    def _adapter_processed_channel(self) -> int:
        return getattr(self._adapter, "_processed_channel_index", 0)
