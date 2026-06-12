from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from utils.logger import get_logger
from utils.subprocess_registry import track_subprocess, untrack_subprocess

logger = get_logger(__name__)


class AudioOutputAdapter:
    """Play WAV audio through PipeWire (`paplay`), optionally targeting a Bluetooth sink."""

    def __init__(
        self,
        bluetooth_mac: Optional[str] = None,
        pulse_sink: Optional[str] = None,
    ) -> None:
        self._bt_mac = bluetooth_mac
        self._sink = pulse_sink
        self._lock = asyncio.Lock()
        self._current_proc: Optional[asyncio.subprocess.Process] = None

    @property
    def bluetooth_mac(self) -> Optional[str]:
        return self._bt_mac

    async def is_bluetooth_connected(self) -> bool:
        if not self._bt_mac or not shutil.which("bluetoothctl"):
            return False
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "info", self._bt_mac,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return "Connected: yes" in (stdout or b"").decode("utf-8", errors="replace")

    async def ensure_bluetooth(self, timeout_seconds: float = 15.0) -> bool:
        if not self._bt_mac:
            return True
        if not shutil.which("bluetoothctl"):
            logger.warning("bluetoothctl not available; cannot ensure speaker connection")
            return False
        if await self.is_bluetooth_connected():
            return True

        logger.info("Connecting Bluetooth speaker %s ...", self._bt_mac)
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "connect", self._bt_mac,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            logger.error("Bluetooth connect timed out (%.0fs)", timeout_seconds)
            return False

        connected = await self.is_bluetooth_connected()
        if not connected:
            logger.error("Bluetooth speaker %s reported disconnected after connect", self._bt_mac)
        return connected

    async def play_wav_bytes(self, wav: bytes) -> None:
        if not wav:
            raise ValueError("Cannot play empty WAV payload")
        path = Path(tempfile.mkstemp(suffix=".wav", prefix="koda_out_")[1])
        try:
            path.write_bytes(wav)
            await self.play_wav_file(path)
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    async def play_wav_file(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(str(path))

        async with self._lock:
            cmd = ["paplay"]
            if self._sink:
                cmd += ["--device", self._sink]
            cmd.append(str(path))
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            self._current_proc = proc
            track_subprocess(proc, label="paplay audio output")
            try:
                _, stderr = await proc.communicate()
            except asyncio.CancelledError:
                await self._kill_proc(proc)
                raise
            finally:
                if self._current_proc is proc:
                    self._current_proc = None
                untrack_subprocess(proc)
            if proc.returncode != 0:
                err = (stderr or b"").decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"paplay failed (code {proc.returncode}): {err}")

    async def stop_playback(self) -> None:
        """Stop the currently running paplay process, if any.

        This deliberately does not take ``self._lock``: if playback is holding
        the lock, waiting for it would mean waiting until the sound finishes.
        We kill the process directly so touch/emergency interrupts are instant.
        """
        proc = self._current_proc
        if proc is None or proc.returncode is not None:
            return
        await self._kill_proc(proc)

    @staticmethod
    async def _kill_proc(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=0.5)
            return
        except (ProcessLookupError, asyncio.TimeoutError):
            pass
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except (ProcessLookupError, asyncio.TimeoutError):
            logger.warning("paplay process refused to stop quickly")
