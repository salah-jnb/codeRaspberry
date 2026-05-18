from __future__ import annotations

import asyncio
from enum import IntEnum

from adapters.nextion_adapter import NextionAdapter
from utils.logger import get_logger

logger = get_logger(__name__)


class Expression(IntEnum):
    NEUTRAL = 0
    BLINK_1 = 1
    BLINK_2 = 2
    HAPPY = 3
    LOVE = 4
    ANGRY = 5
    SAD = 6
    SLEEPING = 7
    SURPRISED = 8
    SINGING = 9
    THINKING = 10


class DisplayService:
    """High-level Nextion face controller; serializes commands and manages blink timer."""

    def __init__(self, adapter: NextionAdapter) -> None:
        self._adapter = adapter
        self._lock = asyncio.Lock()
        self._timer_enabled = True
        self._current = Expression.NEUTRAL

    @property
    def current_expression(self) -> Expression:
        return self._current

    async def set_expression(self, expression: Expression, *, freeze_blink: bool = True) -> None:
        async with self._lock:
            try:
                if freeze_blink and self._timer_enabled:
                    await asyncio.to_thread(self._adapter.send, "tm0.en=0")
                    self._timer_enabled = False
                    await asyncio.sleep(0.05)
                await asyncio.to_thread(self._adapter.send, f"p0.pic={int(expression)}")
                self._current = expression
            except Exception:
                logger.exception("Display set_expression(%s) failed", expression.name)

    async def resume_idle(self) -> None:
        async with self._lock:
            try:
                await asyncio.to_thread(self._adapter.send, f"p0.pic={int(Expression.NEUTRAL)}")
                await asyncio.sleep(0.05)
                await asyncio.to_thread(self._adapter.send, "tm0.en=1")
                self._timer_enabled = True
                self._current = Expression.NEUTRAL
            except Exception:
                logger.exception("Display resume_idle failed")

    async def set_brightness(self, percent: int) -> None:
        clamped = max(0, min(100, percent))
        async with self._lock:
            try:
                await asyncio.to_thread(self._adapter.send, f"dim={clamped}")
            except Exception:
                logger.exception("Display set_brightness(%d) failed", clamped)

    async def sleep(self) -> None:
        async with self._lock:
            try:
                await asyncio.to_thread(self._adapter.send, "sleep=1")
            except Exception:
                logger.exception("Display sleep failed")
