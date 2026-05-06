import asyncio

async def check():
    await asyncio.sleep(0.01)
    return {"name": "bluetooth_check", "ok": True, "message": "HC-05 simulated reachable"}
