import asyncio

async def check():
    # placeholder check — in real code try to open device or query driver
    await asyncio.sleep(0.05)
    return {"name": "mic_check", "ok": True, "message": "ReSpeaker adapter simulated OK"}
