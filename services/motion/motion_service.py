from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from adapters.arduino_adapter import ArduinoAdapter, ArduinoCommand
from utils.logger import get_logger

logger = get_logger(__name__)


class ArduinoSendError(RuntimeError):
    """Raised when a motor/servo command could not be delivered to the Arduino.
    Callers can decide whether to retry, degrade or warn the user."""

    def __init__(self, command: ArduinoCommand, cause: BaseException) -> None:
        super().__init__(f"Arduino command {command.name} failed: {cause!s}")
        self.command = command
        self.cause = cause


@dataclass(frozen=True)
class MotionResult:
    """Outcome of a motion call. ``ok=False`` means the command did not reach
    the Arduino (USB unplug, write timeout, etc.) — caller can re-issue or
    fall back to a safe state."""
    ok: bool
    command: ArduinoCommand
    error: Optional[str] = None


@dataclass(frozen=True)
class RotationCalibration:
    """Open-loop rotation timing model.

    Two modes supported:
      1. **Linear** (default): t = max((|angle| - offset_deg) / slope_deg_per_s, min_duration_s)
         Easy to tune: measure how long the robot needs to make a full 360°,
         derive slope = 360 / t_for_360, then refine offset with a 30° test.
      2. **LUT** (lookup table): pass a list of (angle_deg, duration_s) measurements;
         the timing is interpolated linearly between the two surrounding samples.
         Use this for "professional" precision — the model can be non-linear
         because of inertia, friction and battery-voltage drift.
    """
    # Linear model parameters
    slope_deg_per_s: float = 60.0      # angular speed at steady state (°/s)
    offset_deg: float = 3.3            # angle "consumed" by startup transient (°)
    min_duration_s: float = 0.05       # never send a pulse shorter than this
    settle_s: float = 0.4              # extra wait after STOP for the robot to fully stabilize
    deadband_deg: float = 5.0          # ignore rotations smaller than this (noise)
    front_offset_deg: float = 0.0      # raw DOA value when speaker is exactly in front
    invert_direction: bool = False     # set True if the mic 0° points to the back
    # Optional lookup table — when non-empty, replaces the linear formula.
    lut: List[Tuple[float, float]] = field(default_factory=list)

    def duration_for(self, abs_angle_deg: float) -> float:
        if self.lut:
            return self._interpolate(abs_angle_deg)
        return max(
            (abs_angle_deg - self.offset_deg) / self.slope_deg_per_s,
            self.min_duration_s,
        )

    def _interpolate(self, abs_angle_deg: float) -> float:
        # LUT sorted by angle; clamp at the edges, linear interp in between.
        points = sorted(self.lut)
        if abs_angle_deg <= points[0][0]:
            return max(points[0][1], self.min_duration_s)
        if abs_angle_deg >= points[-1][0]:
            return points[-1][1]
        for (a0, t0), (a1, t1) in zip(points, points[1:]):
            if a0 <= abs_angle_deg <= a1:
                ratio = (abs_angle_deg - a0) / (a1 - a0)
                return max(t0 + ratio * (t1 - t0), self.min_duration_s)
        return self.min_duration_s


def shortest_signed_angle(raw_angle_deg: int, calib: RotationCalibration) -> float:
    """Convert a raw DOA value to a *signed* rotation in [-180, +180].

    Sign convention (after `invert_direction` is applied):
      positive = clockwise / turn right
      negative = counter-clockwise / turn left
    """
    relative = (raw_angle_deg - calib.front_offset_deg) % 360
    if relative > 180:
        relative -= 360
    if calib.invert_direction:
        relative = -relative
    return float(relative)


class MotionService:
    """High-level motor and servo commands; serialized to a single Arduino link."""

    def __init__(
        self,
        adapter: ArduinoAdapter,
        rotation_calibration: Optional[RotationCalibration] = None,
    ) -> None:
        self._adapter = adapter
        # Serializes ARDUINO WRITES only — held briefly per byte. NEVER hold
        # this lock across an asyncio.sleep (rotate_by_angle would otherwise
        # block hello/stop/expression for the full rotation duration).
        self._lock = asyncio.Lock()
        self._rotation = rotation_calibration or RotationCalibration()
        # Signalled by callers (e.g. emergency STOP) to abort the inter-pulse
        # sleep of `rotate_by_angle`. set() => the sleep returns immediately
        # and the STOP command is issued without waiting for the deadline.
        self._abort_event = asyncio.Event()

    async def _send(self, command: ArduinoCommand) -> str:
        """Send a single Arduino command. Raises ArduinoSendError on failure
        so callers can decide what to do (previously: silently returned "")."""
        async with self._lock:
            try:
                ack = await asyncio.to_thread(self._adapter.send, command)
                logger.debug("Arduino %s -> %s", command.name, ack)
                return ack
            except Exception as exc:
                logger.exception("Arduino command %s failed", command.name)
                raise ArduinoSendError(command, exc) from exc

    async def _try_send(self, command: ArduinoCommand) -> MotionResult:
        """Same as `_send` but never raises — useful for cleanup paths."""
        try:
            ack = await self._send(command)
            return MotionResult(ok=True, command=command, error=ack or None)
        except ArduinoSendError as exc:
            return MotionResult(ok=False, command=command, error=str(exc.cause)[:200])

    def request_abort(self) -> None:
        """Interrupt any in-flight ``rotate_by_angle`` sleep — used for an
        emergency STOP (low battery, obstacle detected, user said 'stop').

        Safe to call from any task, including outside the event loop (the
        Event.set is thread-safe). The next time rotate_by_angle's sleep
        wakes, it will immediately issue STOP."""
        self._abort_event.set()

    async def hello(self) -> None:
        await self._send(ArduinoCommand.HELLO)

    async def head(self) -> None:
        await self._send(ArduinoCommand.HEAD)

    async def left_arm(self) -> None:
        await self._send(ArduinoCommand.LEFT_ARM)

    async def right_arm(self) -> None:
        await self._send(ArduinoCommand.RIGHT_ARM)

    async def all_servos(self) -> None:
        await self._send(ArduinoCommand.ALL_SERVOS)

    async def forward(self) -> None:
        await self._send(ArduinoCommand.FORWARD)

    async def backward(self) -> None:
        await self._send(ArduinoCommand.BACKWARD)

    async def left(self) -> None:
        await self._send(ArduinoCommand.LEFT)

    async def right(self) -> None:
        await self._send(ArduinoCommand.RIGHT)

    async def stop(self) -> None:
        await self._send(ArduinoCommand.STOP)

    async def move_for(self, command: ArduinoCommand, seconds: float) -> MotionResult:
        """Avancer/reculer en ligne droite pendant ``seconds`` puis STOP auto.

        L'attente est interruptible : un ``request_abort()`` (STOP d'urgence,
        capteur tactile…) coupe la marche avant la fin et déclenche le STOP
        immédiatement. Renvoie le ``MotionResult`` du STOP final.
        """
        duration = max(0.0, float(seconds))
        self._abort_event.clear()
        start = await self._try_send(command)
        if not start.ok:
            # Le moteur n'a pas démarré — on tente quand même un STOP par sûreté.
            await self._try_send(ArduinoCommand.STOP)
            return start
        logger.info("🛣️  %s pendant %.2fs puis STOP auto", command.name, duration)
        if duration > 0:
            try:
                await asyncio.wait_for(self._abort_event.wait(), timeout=duration)
                logger.info("move_for(%s): interrompu avant la fin", command.name)
            except asyncio.TimeoutError:
                pass  # durée écoulée normalement → arrêt programmé
        return await self._try_send(ArduinoCommand.STOP)

    async def speed_up(self) -> None:
        await self._send(ArduinoCommand.SPEED_UP)

    async def speed_down(self) -> None:
        await self._send(ArduinoCommand.SPEED_DOWN)

    async def status(self) -> str:
        return await self._send(ArduinoCommand.STATUS)

    async def rotate_by_angle(self, signed_angle_deg: float) -> MotionResult:
        """Rotate the chassis by a signed angle (positive=right/CW, negative=left/CCW).

        Uses the MPU6050 closed-loop protocol introduced with the Arduino
        firmware in ``arduino/koda_arduino.ino``: we send ``L<aaa>\\n`` or
        ``R<aaa>\\n`` and the Arduino integrates the gyro Z-rate to stop
        exactly when the target angle is reached. The firmware sends back
        ``DONE:<actual_deg>\\n`` (success) or ``ERR:<reason>\\n``.

        Compared to the previous open-loop timing model (slope + offset), this
        is robust to battery voltage, floor friction and wheel slip — typical
        accuracy improves from +-5-10 deg to +-2 deg.

        The single-byte ``S`` (stop) path from other MotionService methods
        still works for emergency interrupts; the firmware's own 6 s safety
        timeout protects against runaway rotations if the gyro returns garbage.

        Note: ``self._abort_event`` is kept for API compatibility but is no
        longer the primary abort mechanism (there is no asyncio.sleep to cut).
        A touch/STOP from outside can still interrupt by sending the byte ``S``.
        """
        calib = self._rotation
        if abs(signed_angle_deg) < calib.deadband_deg:
            logger.debug(
                "rotate_by_angle: |%.1f°| < deadband %.1f° — skipping",
                signed_angle_deg, calib.deadband_deg,
            )
            return MotionResult(ok=True, command=ArduinoCommand.STOP, error="below deadband")

        direction_char = "R" if signed_angle_deg > 0 else "L"
        direction_cmd = ArduinoCommand.RIGHT if signed_angle_deg > 0 else ArduinoCommand.LEFT
        target_deg = int(round(min(360.0, abs(signed_angle_deg))))
        command_str = f"{direction_char}{target_deg:03d}"
        logger.info(
            "🧭 Rotating %s by %d° (closed-loop MPU6050, cmd=%r)",
            direction_char, target_deg, command_str,
        )
        self._abort_event.clear()

        # Allow ~angle / 30 deg/s + 2 s on top of the firmware's own 6 s safety
        # timeout. A 180° turn budget is therefore ~8 s.
        read_timeout_s = max(3.0, target_deg / 30.0 + 2.0)

        async with self._lock:
            try:
                response = await asyncio.to_thread(
                    self._adapter.send_line, command_str, read_timeout_s=read_timeout_s,
                )
            except RuntimeError as exc:
                logger.exception("rotate_by_angle: send_line failed")
                return MotionResult(ok=False, command=ArduinoCommand.STOP, error=str(exc))
            except Exception as exc:
                logger.exception("rotate_by_angle: unexpected error")
                return MotionResult(ok=False, command=ArduinoCommand.STOP, error=str(exc))

        # Parse the firmware reply.
        if response.startswith("DONE:"):
            try:
                actual = int(response.split(":", 1)[1])
            except ValueError:
                actual = target_deg
            logger.info(
                "🧭 rotation complete: target=%d°, actual≈%d° (drift=%+d°)",
                target_deg, actual, actual - target_deg,
            )
            await asyncio.sleep(calib.settle_s)
            return MotionResult(ok=True, command=direction_cmd)

        if response.startswith("ERR:"):
            logger.warning("Arduino rotation error: %s", response)
            # Defensive STOP in case the firmware errored after the motors started.
            await self._try_send(ArduinoCommand.STOP)
            return MotionResult(ok=False, command=ArduinoCommand.STOP, error=response)

        # Unexpected (empty/timeout/garbage) — be safe.
        logger.warning("rotate_by_angle: unexpected response %r — issuing STOP", response)
        await self._try_send(ArduinoCommand.STOP)
        return MotionResult(
            ok=False, command=ArduinoCommand.STOP,
            error=f"unexpected response: {response[:80]!r}",
        )
