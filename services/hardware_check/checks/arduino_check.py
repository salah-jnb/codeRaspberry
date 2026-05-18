from __future__ import annotations

import asyncio
import glob
import os


async def check() -> dict:
    name = "arduino_check"
    configured = os.environ.get("ARDUINO_PORT", "").strip()

    await asyncio.sleep(0)

    if configured:
        if os.path.exists(configured):
            return {"name": name, "ok": True, "message": f"Arduino port available: {configured}"}
        return {"name": name, "ok": False, "message": f"Configured ARDUINO_PORT not found: {configured}"}

    candidates = sorted(set(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")))
    if not candidates:
        return {"name": name, "ok": False, "message": "No USB/ACM serial device found"}

    return {"name": name, "ok": True, "message": f"Serial port(s) available: {', '.join(candidates)}"}
