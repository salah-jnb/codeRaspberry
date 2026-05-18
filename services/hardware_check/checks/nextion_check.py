from __future__ import annotations

import asyncio
import os
from pathlib import Path


async def check() -> dict:
    name = "nextion_check"
    port = os.environ.get("NEXTION_PORT", "/dev/serial0").strip()
    require_gpio = os.environ.get("NEXTION_REQUIRE_GPIO_UART", "1").strip().lower() in {"1", "true", "yes"}

    await asyncio.sleep(0)

    if not port:
        return {"name": name, "ok": False, "message": "NEXTION_PORT is empty"}

    if not os.path.exists(port):
        return {"name": name, "ok": False, "message": f"Nextion port not found: {port}"}

    resolved = str(Path(port).resolve())
    if require_gpio and not (resolved.startswith("/dev/ttyS") or resolved.startswith("/dev/ttyAMA")):
        return {
            "name": name,
            "ok": False,
            "message": (
                f"NEXTION_PORT resolves to {resolved}, not a GPIO UART node "
                "(expected /dev/ttyS* or /dev/ttyAMA* for pins 8/10)"
            ),
        }

    return {"name": name, "ok": True, "message": f"Nextion port available: {port} -> {resolved}"}
