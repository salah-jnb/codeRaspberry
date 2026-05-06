import asyncio
import platform

async def check():
    await asyncio.sleep(0.01)
    info = platform.platform()
    return {"name": "system_check", "ok": True, "message": f"System: {info}"}
