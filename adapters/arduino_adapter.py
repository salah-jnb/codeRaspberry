from __future__ import annotations

import glob
import time
from enum import Enum
from threading import Lock
from typing import Iterable, Optional

import serial
from serial.serialutil import SerialException

from utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_PORT_CANDIDATES: tuple[str, ...] = ("/dev/ttyUSB0", "/dev/ttyACM0")


class ArduinoCommand(str, Enum):
    HEAD = "T"
    LEFT_ARM = "G"
    RIGHT_ARM = "D"
    ALL_SERVOS = "A"
    HELLO = "H"
    FORWARD = "F"
    BACKWARD = "B"
    LEFT = "L"
    RIGHT = "R"
    STOP = "S"
    SPEED_UP = "+"
    SPEED_DOWN = "-"
    STATUS = "?"


class ArduinoAdapter:
    """Single-character serial protocol to the Arduino UNO + L293D shield."""

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 9600,
        timeout: float = 2.0,
        boot_delay_seconds: float = 2.0,
        require_ack: bool = False,
    ) -> None:
        self._configured_port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._boot_delay = boot_delay_seconds
        self._require_ack = require_ack
        self._serial: Optional[serial.Serial] = None
        self._active_port: Optional[str] = None
        self._lock = Lock()

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def port(self) -> Optional[str]:
        return self._active_port

    def open(self) -> None:
        if self.is_open:
            return
        ports = self._candidate_ports()
        last_error: Optional[Exception] = None
        for candidate in ports:
            try:
                ser = serial.Serial(
                    candidate,
                    self._baudrate,
                    timeout=self._timeout,
                    write_timeout=self._timeout,
                )
            except SerialException as exc:
                last_error = exc
                continue
            time.sleep(self._boot_delay)
            self._flush_buffers(ser)
            self._serial = ser
            self._active_port = candidate
            logger.info("Arduino opened on %s", candidate)
            return

        raise RuntimeError(
            f"Cannot open Arduino on any of {list(ports)}: {last_error}"
        )

    def close(self) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                try:
                    self._serial.close()
                except SerialException:
                    pass
            self._serial = None
            self._active_port = None

    @staticmethod
    def _flush_buffers(ser: serial.Serial) -> None:
        try:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
        except (SerialException, OSError):
            pass

    def send(self, command: str | ArduinoCommand) -> str:
        """Send a single-character command; optional ACK line if the sketch sends one."""
        if not self.is_open:
            raise RuntimeError("Arduino adapter not opened")
        token = command.value if isinstance(command, ArduinoCommand) else command
        if len(token) != 1:
            raise ValueError("Arduino commands must be a single character")

        with self._lock:
            assert self._serial is not None
            try:
                self._flush_buffers(self._serial)
                self._serial.write(token.encode("ascii"))
                self._serial.flush()
                if not self._require_ack:
                    return ""
                line = self._serial.readline()
            except (SerialException, OSError) as exc:
                raise RuntimeError(f"Arduino I/O failed: {exc}") from exc

        return line.decode("ascii", errors="replace").strip()

    def send_line(self, text: str, *, read_timeout_s: float = 8.0) -> str:
        """Send a multi-character command terminated by ``\\n`` and read one
        response line back from the Arduino.

        Used for the extended rotation protocol (e.g. ``L045``, ``R180``) where
        the firmware blocks until the gyro integration reaches the target angle
        and then sends ``DONE:<actual>\\n`` (or ``ERR:<reason>\\n``).

        ``read_timeout_s`` overrides the serial port's default read timeout
        for the duration of this call — closed-loop rotation can take up to
        ~6 s on a 180° turn, so we bump the timeout above the usual 2 s.
        """
        if not self.is_open:
            raise RuntimeError("Arduino adapter not opened")
        if not text or "\n" in text or "\r" in text:
            raise ValueError("send_line text must be non-empty and contain no newline")

        with self._lock:
            assert self._serial is not None
            previous_timeout = self._serial.timeout
            try:
                self._flush_buffers(self._serial)
                self._serial.write((text + "\n").encode("ascii"))
                self._serial.flush()
                self._serial.timeout = read_timeout_s
                line = self._serial.readline()
            except (SerialException, OSError) as exc:
                raise RuntimeError(f"Arduino I/O failed: {exc}") from exc
            finally:
                # Always restore the original read timeout so subsequent
                # single-byte sends behave as the user configured them.
                try:
                    self._serial.timeout = previous_timeout
                except (SerialException, OSError):
                    pass

        return line.decode("ascii", errors="replace").strip()

    def _candidate_ports(self) -> Iterable[str]:
        if self._configured_port:
            return (self._configured_port,)
        discovered = sorted(set(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")))
        if discovered:
            return tuple(discovered)
        return _DEFAULT_PORT_CANDIDATES

    def __enter__(self) -> "ArduinoAdapter":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
