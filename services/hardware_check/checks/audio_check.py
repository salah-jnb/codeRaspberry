from __future__ import annotations

import asyncio
import shutil


async def check() -> dict:
    name = "audio_check"
    if not shutil.which("paplay"):
        return {"name": name, "ok": False, "message": "paplay not found (install pipewire-pulse)"}

    if not shutil.which("wpctl"):
        return {"name": name, "ok": False, "message": "wpctl not found (install wireplumber)"}

    proc = await asyncio.create_subprocess_exec(
        "wpctl", "status",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    text = (out or b"").decode("utf-8", errors="replace")
    err_text = (err or b"").decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        return {"name": name, "ok": False, "message": f"wpctl status failed: {err_text}"}

    if "Sinks:" not in text:
        return {"name": name, "ok": False, "message": "No audio sinks listed by wpctl"}

    sinks_block = text.split("Sinks:", 1)[1].split("Sources:", 1)[0]
    has_default_sink = "*" in sinks_block
    if not has_default_sink:
        return {"name": name, "ok": False, "message": "No default audio sink set"}

    return {"name": name, "ok": True, "message": "Default audio sink available (PipeWire)"}
