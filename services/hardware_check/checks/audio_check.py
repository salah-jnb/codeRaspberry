import asyncio

async def check():
    await asyncio.sleep(0.01)
    return {"name": "audio_check", "ok": True, "message": "Audio output simulated OK"}
