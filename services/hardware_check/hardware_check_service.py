from __future__ import annotations

import asyncio

try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:
    _load_dotenv = None

from services.hardware_check.checks import (
    arduino_check,
    audio_check,
    bluetooth_check,
    camera_check,
    mic_check,
    nextion_check,
    system_check,
)
from utils.logger import get_logger

logger = get_logger(__name__)

_CHECKS = (
    mic_check.check,
    camera_check.check,
    nextion_check.check,
    arduino_check.check,
    bluetooth_check.check,
    audio_check.check,
    system_check.check,
)


async def run_full_check() -> list[dict]:
    tasks = [asyncio.create_task(fn()) for fn in _CHECKS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    statuses: list[dict] = []
    for result, fn in zip(results, _CHECKS):
        module_name = getattr(fn, "__module__", "")
        check_name = module_name.split(".")[-1] if module_name else getattr(fn, "__name__", str(fn))
        if isinstance(result, Exception):
            logger.exception("Check %s raised", check_name)
            statuses.append({"name": check_name, "ok": False, "message": str(result)})
        else:
            statuses.append(result)
    return statuses


if __name__ == "__main__":
    if _load_dotenv is not None:
        _load_dotenv()
    for status in asyncio.run(run_full_check()):
        print(status)
