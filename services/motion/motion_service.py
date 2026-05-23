from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from adapters.arduino_adapter import ArduinoAdapter, ArduinoCommand
from utils.logger import get_logger

logger = get_logger(__name__)


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
        self._lock = asyncio.Lock()
        self._rotation = rotation_calibration or RotationCalibration()

    async def _send(self, command: ArduinoCommand) -> str:
        async with self._lock:
            try:
                ack = await asyncio.to_thread(self._adapter.send, command)
                logger.debug("Arduino %s -> %s", command.name, ack)
                return ack
            except Exception:
                logger.exception("Arduino command %s failed", command.name)
                return ""

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

    async def speed_up(self) -> None:
        await self._send(ArduinoCommand.SPEED_UP)

    async def speed_down(self) -> None:
        await self._send(ArduinoCommand.SPEED_DOWN)

    async def status(self) -> str:
        return await self._send(ArduinoCommand.STATUS)

    async def rotate_by_angle(self, signed_angle_deg: float) -> None:
        """Rotate the chassis by a signed angle (positive=right/CW, negative=left/CCW).

        Open-loop timing: the duration comes from the linear model or the LUT in
        the active `RotationCalibration`. Below the deadband nothing is sent —
        we don't burn battery on imperceptible 1° corrections.
        """
        calib = self._rotation
        if abs(signed_angle_deg) < calib.deadband_deg:
            logger.debug(
                "rotate_by_angle: |%.1f°| < deadband %.1f° — skipping",
                signed_angle_deg, calib.deadband_deg,
            )
            return
        direction = ArduinoCommand.RIGHT if signed_angle_deg > 0 else ArduinoCommand.LEFT
        duration = calib.duration_for(abs(signed_angle_deg))
        logger.info(
            "🧭 Rotating %s by %.1f° (duration=%.2fs, calib=%s)",
            direction.name, signed_angle_deg, duration,
            "LUT" if calib.lut else f"linear slope={calib.slope_deg_per_s} offset={calib.offset_deg}",
        )
        async with self._lock:
            try:
                await asyncio.to_thread(self._adapter.send, direction)
                await asyncio.sleep(duration)
                await asyncio.to_thread(self._adapter.send, ArduinoCommand.STOP)
            except Exception:
                logger.exception("rotate_by_angle: motor command failed")
                try:
                    await asyncio.to_thread(self._adapter.send, ArduinoCommand.STOP)
                except Exception:
                    pass
        await asyncio.sleep(calib.settle_s)
