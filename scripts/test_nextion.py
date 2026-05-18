"""Cycle through every Nextion expression with a short hold between each.

Run:
    python -m scripts.test_nextion
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.nextion_adapter import NextionAdapter
from app.config import load_config
from services.display.display_service import DisplayService, Expression
from utils.logger import get_logger

logger = get_logger("test_nextion")


async def main() -> int:
    config = load_config()
    adapter = NextionAdapter(
        port=config.nextion.port,
        baudrate=config.nextion.baudrate,
        timeout=config.nextion.timeout_seconds,
    )

    try:
        adapter.open()
    except RuntimeError as exc:
        logger.error("Cannot open Nextion: %s", exc)
        return 1

    display = DisplayService(adapter)
    try:
        logger.info("Resuming idle (blink ON)")
        await display.resume_idle()
        await asyncio.sleep(1.0)

        ordered = [
            Expression.HAPPY,
            Expression.LOVE,
            Expression.SURPRISED,
            Expression.THINKING,
            Expression.SINGING,
            Expression.SAD,
            Expression.ANGRY,
            Expression.SLEEPING,
        ]
        for expr in ordered:
            logger.info("Setting expression %s (pic=%d)", expr.name, int(expr))
            await display.set_expression(expr)
            await asyncio.sleep(1.2)

        logger.info("Setting brightness to 60%%")
        await display.set_brightness(60)
        await asyncio.sleep(0.5)

        logger.info("Returning to idle (blink ON)")
        await display.resume_idle()
    finally:
        adapter.close()

    logger.info("Nextion test complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
