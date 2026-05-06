import asyncio
import os
import shutil

async def check():
    name = "bluetooth_check"
    # prefer bluetoothctl if available
    if not shutil.which("bluetoothctl"):
        return {"name": name, "ok": False, "message": "bluetoothctl not available"}

    show_proc = await asyncio.create_subprocess_exec(
        "bluetoothctl", "show",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await show_proc.communicate()
    out_text = (out or b"").decode(errors="ignore")
    err_text = (err or b"").decode(errors="ignore")

    if show_proc.returncode != 0:
        return {"name": name, "ok": False, "message": f"bluetoothctl error: {err_text.strip()}"}

    if "Controller" not in out_text:
        return {"name": name, "ok": False, "message": "No Bluetooth controller detected"}

    hc05_mac = os.environ.get("HC05_MAC", "").strip()
    if not hc05_mac:
        return {
            "name": name,
            "ok": False,
            "message": "Controller present, but HC05_MAC not set so HC-05 cannot be confirmed",
        }

    info_proc = await asyncio.create_subprocess_exec(
        "bluetoothctl", "info", hc05_mac,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    info_out, info_err = await info_proc.communicate()
    info_text = (info_out or b"").decode(errors="ignore")
    info_err_text = (info_err or b"").decode(errors="ignore")

    if info_proc.returncode != 0 and not info_text.strip():
        return {"name": name, "ok": False, "message": f"HC-05 info unavailable for {hc05_mac}: {info_err_text.strip()}"}

    is_connected = "Connected: yes" in info_text
    is_paired = "Paired: yes" in info_text
    if is_connected:
        return {"name": name, "ok": True, "message": f"HC-05 connected ({hc05_mac})"}
    if is_paired:
        return {"name": name, "ok": False, "message": f"HC-05 paired but not connected ({hc05_mac})"}
    return {"name": name, "ok": False, "message": f"HC-05 not connected ({hc05_mac})"}
