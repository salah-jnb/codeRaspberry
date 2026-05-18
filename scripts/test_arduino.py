"""Send a controlled sequence of commands to the Arduino + L293D shield.

Run:
    python -m scripts.test_arduino           # safe: only servos
    python -m scripts.test_arduino --motors  # also moves DC motors briefly
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.arduino_adapter import ArduinoAdapter
from app.config import load_config
from services.motion.motion_service import MotionService
from utils.logger import get_logger

logger = get_logger("test_arduino")


async def main(include_motors: bool) -> int:
    config = load_config()
    adapter = ArduinoAdapter(
        port=config.arduino.port,
        baudrate=config.arduino.baudrate,
        timeout=config.arduino.timeout_seconds,
        boot_delay_seconds=config.arduino.boot_delay_seconds,
    )

    try:
        adapter.open()
    except RuntimeError as exc:
        logger.error("Cannot open Arduino: %s", exc)
        return 1

    motion = MotionService(adapter)
    try:
        logger.info("Status: %s", await motion.status())
        await asyncio.sleep(0.3)

        logger.info("Hello gesture (3 nods)")
        await motion.hello()
        await asyncio.sleep(2.0)

        logger.info("Head sweep")
        await motion.head()
        await asyncio.sleep(2.0)

        logger.info("Left arm")
        await motion.left_arm()
        await asyncio.sleep(1.5)

        logger.info("Right arm")
        await motion.right_arm()
        await asyncio.sleep(1.5)

        logger.info("Both servos")
        await motion.all_servos()
        await asyncio.sleep(2.0)

        if include_motors:
            logger.info("Forward briefly")
            await motion.forward()
            await asyncio.sleep(0.5)
            await motion.stop()
            await asyncio.sleep(0.5)

            logger.info("Backward briefly")
            await motion.backward()
            await asyncio.sleep(0.5)
            await motion.stop()
            await asyncio.sleep(0.5)

            logger.info("Rotate left")
            await motion.left()
            await asyncio.sleep(0.5)
            await motion.stop()
            await asyncio.sleep(0.5)

            logger.info("Rotate right")
            await motion.right()
            await asyncio.sleep(0.5)
            await motion.stop()

        logger.info("Final stop")
        await motion.stop()
    finally:
        adapter.close()

    logger.info("Arduino test complete")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--motors", action="store_true", help="Also run DC motor commands briefly")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.motors)))
