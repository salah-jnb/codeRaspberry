import asyncio
import shutil

async def check():
    """Check for available capture devices using `arecord -l`.

    Returns dict with name, ok, message.
    """
    name = "mic_check"
    # ensure arecord exists
    if not shutil.which("arecord"):
        return {"name": name, "ok": False, "message": "arecord not found (alsa-utils not installed)"}

    proc = await asyncio.create_subprocess_exec(
        "arecord", "-l",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    out_text = (out or b"").decode(errors="ignore")
    err_text = (err or b"").decode(errors="ignore")

    if proc.returncode != 0:
        return {"name": name, "ok": False, "message": f"arecord error: {err_text.strip()}"}

    if "card" in out_text.lower():
        return {"name": name, "ok": True, "message": "Capture device(s) detected"}
    else:
        return {"name": name, "ok": False, "message": "No capture devices listed by arecord"}
