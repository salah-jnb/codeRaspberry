import asyncio

async def check():
    await asyncio.sleep(0.01)
    return {"name": "nextion_check", "ok": True, "message": "Nextion adapter simulated OK"}
