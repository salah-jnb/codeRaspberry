import asyncio
import os

async def check():
    name = "camera_check"
    # quick non-blocking file-system check for video device
    await asyncio.sleep(0)
    devs = ["/dev/video0", "/dev/video1"]
    for d in devs:
        if os.path.exists(d):
            return {"name": name, "ok": True, "message": f"Camera device found: {d}"}

    return {"name": name, "ok": False, "message": "No /dev/video* devices found"}
