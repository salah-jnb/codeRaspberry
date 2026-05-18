from __future__ import annotations

import asyncio
import os
import re
import shutil


_MAC_RE = re.compile(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")


async def check() -> dict:
    name = "bluetooth_check"
    mac = os.environ.get("BLUETOOTH_MAC", "CB:7A:DB:AD:30:D9").strip()

    if not shutil.which("bluetoothctl"):
        return {"name": name, "ok": False, "message": "bluetoothctl not available (install bluez)"}

    if not mac:
        return {"name": name, "ok": False, "message": "BLUETOOTH_MAC not configured"}

    if not _MAC_RE.fullmatch(mac):
        return {"name": name, "ok": False, "message": f"Invalid BLUETOOTH_MAC format: {mac}"}
    mac = mac.upper()

    show = await asyncio.create_subprocess_exec(
        "bluetoothctl", "show",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    show_out, _ = await show.communicate()
    if "Controller" not in (show_out or b"").decode("utf-8", errors="replace"):
        return {"name": name, "ok": False, "message": "No Bluetooth controller detected"}

    info = await asyncio.create_subprocess_exec(
        "bluetoothctl", "info", mac,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    info_out, info_err = await info.communicate()
    info_text = (info_out or b"").decode("utf-8", errors="replace")

    if not info_text.strip():
        err = (info_err or b"").decode("utf-8", errors="replace").strip()
        return {"name": name, "ok": False, "message": f"BT speaker info unavailable for {mac}: {err}"}

    if "Connected: yes" in info_text:
        return {"name": name, "ok": True, "message": f"BT speaker connected ({mac})"}
    if "Paired: yes" in info_text:
        return {"name": name, "ok": False, "message": f"BT speaker paired but not connected ({mac})"}
    return {"name": name, "ok": False, "message": f"BT speaker not connected ({mac})"}
