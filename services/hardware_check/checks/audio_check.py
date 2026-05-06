import asyncio
import shutil

async def check():
    name = "audio_check"
    if not shutil.which("aplay"):
        return {"name": name, "ok": False, "message": "aplay not found (alsa-utils not installed)"}

    proc = await asyncio.create_subprocess_exec(
        "aplay", "-l",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    out_text = (out or b"").decode(errors="ignore")
    err_text = (err or b"").decode(errors="ignore")

    if proc.returncode != 0:
        return {"name": name, "ok": False, "message": f"aplay error: {err_text.strip()}"}

    if "card" in out_text.lower():
        return {"name": name, "ok": True, "message": "Audio playback device(s) detected"}
    else:
        return {"name": name, "ok": False, "message": "No playback devices listed by aplay"}
