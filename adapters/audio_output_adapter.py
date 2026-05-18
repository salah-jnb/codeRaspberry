from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

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
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = (stderr or b"").decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"paplay failed (code {proc.returncode}): {err}")
