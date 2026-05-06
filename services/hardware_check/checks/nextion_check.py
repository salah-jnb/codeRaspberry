import asyncio
import glob
import os

async def check():
    name = "nextion_check"
    # To avoid false positives, prefer explicit port from env.
    expected_port = os.environ.get("NEXTION_PORT", "").strip()
    await asyncio.sleep(0)

    if expected_port:
        if os.path.exists(expected_port):
            return {"name": name, "ok": True, "message": f"Nextion detected on configured port: {expected_port}"}
        return {"name": name, "ok": False, "message": f"Configured NEXTION_PORT not found: {expected_port}"}

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

    return {"name": name, "ok": False, "message": "No USB serial devices found for Nextion"}
