import asyncio
import shutil

async def check():
    name = "bluetooth_check"
    # prefer bluetoothctl if available
    if not shutil.which("bluetoothctl"):
        return {"name": name, "ok": False, "message": "bluetoothctl not available"}

    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl", "show",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    out_text = (out or b"").decode(errors="ignore")
    err_text = (err or b"").decode(errors="ignore")

    if proc.returncode != 0:
        return {"name": name, "ok": False, "message": f"bluetoothctl error: {err_text.strip()}"}

    if "Controller" in out_text or "Powered" in out_text:
        return {"name": name, "ok": True, "message": "Bluetooth controller present"}
    return {"name": name, "ok": False, "message": "No Bluetooth controller detected"}
