import asyncio
import os
import re
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

    # Strict matching by ALSA card index (recommended for fixed hardware setup)
    # Example line: "card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]"
    card_pattern = re.compile(r"card\s+(\d+):\s*([^\[]+)\[([^\]]+)\]", re.IGNORECASE)
    cards = []
    for line in out_text.splitlines():
        match = card_pattern.search(line)
        if match:
            cards.append({
                "index": match.group(1).strip(),
                "short": match.group(2).strip(),
                "label": match.group(3).strip(),
                "raw": line.strip(),
            })

    card_index = os.environ.get("AUDIO_CARD_INDEX", "").strip()
    card_label = os.environ.get("AUDIO_CARD_LABEL", "").strip().lower()
    if card_index:
        for card in cards:
            if card["index"] == card_index:
                return {
                    "name": name,
                    "ok": True,
                    "message": f"Configured ALSA card index detected: card {card_index} ({card['label']})",
                }
        detected = ", ".join(f"{c['index']}:{c['label']}" for c in cards) or "none"
        return {
            "name": name,
            "ok": False,
            "message": f"Configured AUDIO_CARD_INDEX={card_index} not found. Detected cards: {detected}",
        }

    if card_label:
        for card in cards:
            haystack = f"{card['short']} {card['label']} {card['raw']}".lower()
            if card_label in haystack:
                return {
                    "name": name,
                    "ok": True,
                    "message": f"Configured ALSA card label matched: {card['label']}",
                }
        return {
            "name": name,
            "ok": False,
            "message": f"Configured AUDIO_CARD_LABEL not found: {card_label}",
        }

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
