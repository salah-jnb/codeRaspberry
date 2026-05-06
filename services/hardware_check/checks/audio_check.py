import asyncio
import os
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

    if "card" not in out_text.lower():
        return {"name": name, "ok": False, "message": "No playback devices listed by aplay"}

    keyword = os.environ.get("AUDIO_DEVICE_KEYWORD", "").strip().lower()
    if keyword:
        if keyword in out_text.lower():
            return {"name": name, "ok": True, "message": f"Configured audio device keyword matched: {keyword}"}
        return {"name": name, "ok": False, "message": f"Audio device present but keyword not found: {keyword}"}

    return {
        "name": name,
        "ok": False,
        "message": "Audio interface detected, but amplifier (PAM8403) cannot be auto-verified. Set AUDIO_DEVICE_KEYWORD for strict check.",
    }
