import asyncio

async def check():
    await asyncio.sleep(0.05)
    return {"name": "camera_check", "ok": True, "message": "Camera adapter simulated OK"}
