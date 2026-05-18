from __future__ import annotations

from threading import Lock
from typing import Optional

import serial
from serial.serialutil import SerialException

from utils.logger import get_logger

logger = get_logger(__name__)

_END = b"\xff\xff\xff"


class NextionAdapter:
    """Send Nextion HMI commands over UART (default /dev/serial0 @ 9600)."""

    def __init__(
        self,
        port: str = "/dev/serial0",
        baudrate: int = 9600,
        timeout: float = 1.0,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._serial: Optional[serial.Serial] = None
        self._lock = Lock()

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def open(self) -> None:
        if self.is_open:
            return
        try:
            self._serial = serial.Serial(
                self._port,
                self._baudrate,
                timeout=self._timeout,
                write_timeout=self._timeout,
            )
        except SerialException as exc:
            self._serial = None
            raise RuntimeError(f"Cannot open Nextion port {self._port}: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                try:
                    self._serial.close()
                except SerialException:
                    pass
            self._serial = None

    def send(self, command: str) -> None:
        if not self.is_open:
            raise RuntimeError("Nextion adapter not opened")
        payload = command.encode("ascii", errors="replace") + _END
        with self._lock:
            assert self._serial is not None
            try:
                self._serial.write(payload)
                self._serial.flush()
            except SerialException as exc:
                logger.error("Nextion write failed: %s", exc)
                raise RuntimeError(f"Nextion write failed: {exc}") from exc

    def __enter__(self) -> "NextionAdapter":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
