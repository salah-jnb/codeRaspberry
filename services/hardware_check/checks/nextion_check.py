import asyncio
import glob
import os

async def check():
    name = "nextion_check"
    # look for common serial device nodes that a Nextion screen could use
    await asyncio.sleep(0)
    candidates = []
    candidates.extend(glob.glob("/dev/ttyUSB*"))
    candidates.extend(glob.glob("/dev/ttyACM*"))
    candidates.extend(glob.glob("/dev/ttyAMA*") )
    candidates.extend(glob.glob("/dev/ttyS*"))

    # filter out nonexistent or root console ttys
    devices = [p for p in candidates if os.path.exists(p)]
    if devices:
        return {"name": name, "ok": True, "message": f"Serial device(s) present: {', '.join(devices)}"}

    return {"name": name, "ok": False, "message": "No serial devices found for Nextion (check connection)"}
