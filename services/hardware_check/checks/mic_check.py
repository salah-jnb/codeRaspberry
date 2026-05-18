from __future__ import annotations

import asyncio
import os
import re
import shutil


async def check() -> dict:
    name = "mic_check"
    if not shutil.which("arecord"):
        return {"name": name, "ok": False, "message": "arecord not found (install alsa-utils)"}

    proc = await asyncio.create_subprocess_exec(
        "arecord", "-l",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    out_text = (out or b"").decode("utf-8", errors="replace")
    err_text = (err or b"").decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        return {"name": name, "ok": False, "message": f"arecord error: {err_text}"}

    if "card" not in out_text.lower():
        return {"name": name, "ok": False, "message": "No capture devices listed by arecord"}

    keyword = os.environ.get("RESPEAKER_KEYWORD", "seeed").strip().lower()
    if keyword and keyword in out_text.lower():
        index_match = re.search(
            rf"card\s+(\d+):[^\n]*{re.escape(keyword)}",
            out_text,
            re.IGNORECASE,
        )
        index = index_match.group(1) if index_match else "?"
        return {
            "name": name,
            "ok": True,
            "message": f"ReSpeaker detected on card {index}",
        }
    return {
        "name": name,
        "ok": False,
        "message": f"Capture device(s) detected but '{keyword}' not found — verify RESPEAKER_DEVICE",
    }
