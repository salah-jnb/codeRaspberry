"""Regression tests for the MotionService closed-loop rotation.

The rotation now delegates timing to the Arduino MPU6050 firmware:
the Pi sends ``L045`` / ``R180`` via send_line and waits for
``DONE:<actual>`` (or ``ERR:<reason>``). Tests use a fake adapter
to validate the protocol + error paths without hardware.
"""

from __future__ import annotations

import asyncio
import time
from typing import List

import pytest

from adapters.arduino_adapter import ArduinoCommand
from services.motion.motion_service import (
    ArduinoSendError,
    MotionResult,
    MotionService,
    RotationCalibration,
)


class _FakeArduinoAdapter:
    """Records every send / send_line and lets tests inject the gyro reply."""

    def __init__(self, rotation_reply: str = "DONE:45", rotation_delay_s: float = 0.0) -> None:
        self.sends: List[tuple[float, ArduinoCommand]] = []
        self.lines: List[tuple[float, str]] = []
        self.is_open = True
        self._t0 = time.perf_counter()
        self._rotation_reply = rotation_reply
        self._rotation_delay_s = rotation_delay_s

    def send(self, command):  # noqa: D401
        self.sends.append((time.perf_counter() - self._t0, command))
        return "OK"

    def send_line(self, text: str, *, read_timeout_s: float = 8.0) -> str:
        self.lines.append((time.perf_counter() - self._t0, text))
        # Simulate firmware "blocking until rotation done".
        if self._rotation_delay_s > 0:
            time.sleep(self._rotation_delay_s)
        return self._rotation_reply


@pytest.mark.asyncio
async def test_rotation_sends_extended_line() -> None:
    """rotate_by_angle(+45) must transmit 'R045' via send_line."""
    fake = _FakeArduinoAdapter(rotation_reply="DONE:45")
    svc = MotionService(fake, RotationCalibration(settle_s=0.0, deadband_deg=5.0))

    result = await svc.rotate_by_angle(45.0)
    assert result.ok
    assert result.command == ArduinoCommand.RIGHT
    assert len(fake.lines) == 1
    assert fake.lines[0][1] == "R045"


@pytest.mark.asyncio
async def test_rotation_negative_sends_L() -> None:
    """Negative angle must send L (left) not R."""
    fake = _FakeArduinoAdapter(rotation_reply="DONE:90")
    svc = MotionService(fake, RotationCalibration(settle_s=0.0))
    result = await svc.rotate_by_angle(-90.0)
    assert result.ok
    assert result.command == ArduinoCommand.LEFT
    assert fake.lines[0][1] == "L090"


@pytest.mark.asyncio
async def test_rotation_below_deadband_skips() -> None:
    """No serial traffic for tiny angles (battery saver)."""
    fake = _FakeArduinoAdapter()
    svc = MotionService(fake, RotationCalibration(deadband_deg=5.0, settle_s=0.0))
    result = await svc.rotate_by_angle(3.0)
    assert result.ok
    assert "below deadband" in (result.error or "")
    assert fake.lines == []
    assert fake.sends == []


@pytest.mark.asyncio
async def test_rotation_err_response_returns_failure() -> None:
    """Firmware ERR:* must surface as MotionResult.ok=False and trigger a defensive STOP."""
    fake = _FakeArduinoAdapter(rotation_reply="ERR:timeout")
    svc = MotionService(fake, RotationCalibration(settle_s=0.0))
    result = await svc.rotate_by_angle(180.0)
    assert not result.ok
    assert "ERR:timeout" in (result.error or "")
    # Defensive STOP should have been sent on failure.
    cmds = [c for _, c in fake.sends]
    assert ArduinoCommand.STOP in cmds


@pytest.mark.asyncio
async def test_rotation_unexpected_response_returns_failure() -> None:
    fake = _FakeArduinoAdapter(rotation_reply="garbage")
    svc = MotionService(fake, RotationCalibration(settle_s=0.0))
    result = await svc.rotate_by_angle(90.0)
    assert not result.ok
    assert "garbage" in (result.error or "")


@pytest.mark.asyncio
async def test_other_commands_concurrent_with_pending_rotation() -> None:
    """The asyncio lock is held for the duration of send_line, but the rotation
    runs on the Arduino — meaning the Pi thread is freed quickly. Other
    commands should still queue correctly without deadlock.
    """
    fake = _FakeArduinoAdapter(rotation_reply="DONE:45", rotation_delay_s=0.3)
    svc = MotionService(fake, RotationCalibration(settle_s=0.0))

    rotate_task = asyncio.create_task(svc.rotate_by_angle(45.0))
    # Fire hello after a brief moment.
    await asyncio.sleep(0.05)
    hello_task = asyncio.create_task(svc.hello())

    await asyncio.gather(rotate_task, hello_task)
    assert any(c == ArduinoCommand.HELLO for _, c in fake.sends)
    assert fake.lines[0][1] == "R045"


@pytest.mark.asyncio
async def test_send_raises_on_adapter_failure() -> None:
    """Failed single-byte sends still raise ArduinoSendError (legacy path)."""
    fake = _FakeArduinoAdapter()

    def boom(_command):
        raise OSError("USB unplugged")

    fake.send = boom  # type: ignore[assignment]

    svc = MotionService(fake, RotationCalibration())
    with pytest.raises(ArduinoSendError) as exc:
        await svc.hello()
    assert exc.value.command == ArduinoCommand.HELLO
