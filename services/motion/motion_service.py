from __future__ import annotations

import asyncio

from adapters.arduino_adapter import ArduinoAdapter, ArduinoCommand
from utils.logger import get_logger

logger = get_logger(__name__)


class MotionService:
    """High-level motor and servo commands; serialized to a single Arduino link."""

    def __init__(self, adapter: ArduinoAdapter) -> None:
        self._adapter = adapter
        self._lock = asyncio.Lock()

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
