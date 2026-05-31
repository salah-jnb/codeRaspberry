"""Regression tests for the MotionService lock-during-sleep fix.

Before the fix, ``rotate_by_angle(180)`` would hold the asyncio.Lock for the
full ~3 s rotation duration, blocking every other Arduino command. Now the
lock is only held for the individual serial writes (direction + STOP) and
released during the sleep.
"""

from __future__ import annotations

import asyncio
import time
from typing import List
from unittest.mock import MagicMock

import pytest

from adapters.arduino_adapter import ArduinoCommand
from services.motion.motion_service import (
    ArduinoSendError,
    MotionResult,
    MotionService,
    RotationCalibration,
)


class _FakeArduinoAdapter:
    """Minimal fake — records every send() and the timestamp it happened at."""

    def __init__(self) -> None:
        self.sends: List[tuple[float, ArduinoCommand]] = []
        self.is_open = True
        self._t0 = time.perf_counter()

    def send(self, command):  # noqa: D401
        self.sends.append((time.perf_counter() - self._t0, command))
        return "OK"


@pytest.mark.asyncio
async def test_rotate_releases_lock_during_sleep() -> None:
    """While rotate_by_angle is awaiting the rotation sleep, another command
    must be able to take the lock and execute IMMEDIATELY.

    Before the fix: hello() would wait ~1.5s for rotate to finish.
    After:          hello() executes within ~50 ms of being called.
    """
    fake = _FakeArduinoAdapter()
    calib = RotationCalibration(slope_deg_per_s=60.0, offset_deg=3.3, settle_s=0.0)
    svc = MotionService(fake, calib)

    # 90° at 60°/s ≈ 1.45 s rotation.
    rotate_task = asyncio.create_task(svc.rotate_by_angle(90.0))

    # Give rotate_by_angle a beat to send the direction command and enter sleep.
    await asyncio.sleep(0.1)

    t_hello_start = time.perf_counter()
    await svc.hello()
    hello_wait = time.perf_counter() - t_hello_start

    await rotate_task

    # hello() must NOT wait the whole rotation. Cap at 200 ms (generous on slow CI).
    assert hello_wait < 0.2, (
        f"hello() blocked for {hello_wait * 1000:.0f} ms — lock leak during rotation!"
    )

    # Both commands must have actually been delivered.
    cmds = [c for _, c in fake.sends]
    assert ArduinoCommand.RIGHT in cmds, "Rotation direction not sent"
    assert ArduinoCommand.HELLO in cmds, "hello() not sent"
    assert ArduinoCommand.STOP in cmds, "STOP after rotation not sent"


@pytest.mark.asyncio
async def test_rotate_abort_event_cuts_sleep_short() -> None:
    """request_abort() must terminate the rotation sleep early."""
    fake = _FakeArduinoAdapter()
    calib = RotationCalibration(slope_deg_per_s=60.0, offset_deg=0.0, settle_s=0.0)
    svc = MotionService(fake, calib)

    # 180° at 60°/s = ~3 s. We'll abort after 200 ms.
    t0 = time.perf_counter()
    rotate_task = asyncio.create_task(svc.rotate_by_angle(180.0))
    await asyncio.sleep(0.2)
    svc.request_abort()
    result = await rotate_task
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0, (
        f"abort did not cut the sleep — rotation took {elapsed:.2f}s (expected <1s)"
    )
    assert result.ok, f"rotation result not ok: {result}"
    # STOP must still have been delivered after the abort.
    cmds = [c for _, c in fake.sends]
    assert cmds[-1] == ArduinoCommand.STOP


@pytest.mark.asyncio
async def test_send_raises_on_adapter_failure() -> None:
    """The previous code returned "" on errors. Callers couldn't tell the
    motor didn't move. Now we raise ArduinoSendError."""
    fake = _FakeArduinoAdapter()

    def boom(_command):  # noqa: ANN001
        raise OSError("USB unplugged")

    fake.send = boom  # type: ignore[assignment]

    svc = MotionService(fake, RotationCalibration())
    with pytest.raises(ArduinoSendError) as exc:
        await svc.hello()
    assert exc.value.command == ArduinoCommand.HELLO
    assert isinstance(exc.value.cause, OSError)


@pytest.mark.asyncio
async def test_rotate_below_deadband_returns_skip() -> None:
    """Tiny angles should not fire any motor command (battery saver)."""
    fake = _FakeArduinoAdapter()
    calib = RotationCalibration(deadband_deg=5.0, settle_s=0.0)
    svc = MotionService(fake, calib)

    result = await svc.rotate_by_angle(3.0)  # below deadband
    assert result.ok
    assert "below deadband" in (result.error or "")
    assert fake.sends == [], "should not send anything below deadband"
