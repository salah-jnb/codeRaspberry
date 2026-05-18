from __future__ import annotations

import asyncio
import os
import shutil


async def check() -> dict:
    name = "camera_check"

    if shutil.which("rpicam-hello"):
        proc = await asyncio.create_subprocess_exec(
            "rpicam-hello", "--list-cameras",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        text = (out or b"").decode("utf-8", errors="replace")
        if proc.returncode == 0 and any(tag in text for tag in ("imx", "ov", "Available cameras")):
            if "No cameras available" in text or "0 :" not in text:
                return {"name": name, "ok": False, "message": "rpicam-hello reports no usable camera"}
            return {"name": name, "ok": True, "message": "Camera detected via rpicam-hello"}

    for device in ("/dev/video0", "/dev/video1"):
        if os.path.exists(device):
            return {"name": name, "ok": True, "message": f"Camera device found: {device}"}

    return {"name": name, "ok": False, "message": "No camera detected (sensor faulty or missing)"}
