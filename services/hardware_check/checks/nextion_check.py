import asyncio
import glob
import os
from pathlib import Path

async def check():
    name = "nextion_check"
    # For GPIO RX/TX pins (8/10), use /dev/serial0 (recommended) or ttyS0/ttyAMA0.
    expected_port = os.environ.get("NEXTION_PORT", "/dev/serial0").strip()
    require_gpio_uart = os.environ.get("NEXTION_REQUIRE_GPIO_UART", "1").strip().lower() in {"1", "true", "yes"}
    await asyncio.sleep(0)

    if expected_port:
        if not os.path.exists(expected_port):
            return {"name": name, "ok": False, "message": f"Configured NEXTION_PORT not found: {expected_port}"}

        resolved = str(Path(expected_port).resolve())
        if require_gpio_uart and not (resolved.startswith("/dev/ttyS") or resolved.startswith("/dev/ttyAMA")):
            return {
                "name": name,
                "ok": False,
                "message": (
                    f"NEXTION_PORT resolves to {resolved}, not a GPIO UART device "
                    "(expected /dev/ttyS* or /dev/ttyAMA* for pins 8/10)"
                ),
            }

        return {
            "name": name,
            "ok": True,
            "message": f"Nextion port available: {expected_port} -> {resolved}",
        }

    # Default probing: only external USB serial nodes, not ttyS0/ttyAMA0.
    candidates = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    devices = [p for p in candidates if os.path.exists(p)]
    if devices:
        return {
            "name": name,
            "ok": False,
            "message": (
                "USB serial device(s) found but Nextion not confirmed. "
                "Set NEXTION_PORT to validate exact device: " + ", ".join(devices)
            ),
        }

    return {"name": name, "ok": False, "message": "No serial devices found for Nextion"}
