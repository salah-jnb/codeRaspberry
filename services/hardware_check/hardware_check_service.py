import asyncio
from utils.logger import get_logger
from services.hardware_check.checks import mic_check, camera_check, nextion_check, bluetooth_check, audio_check, system_check

logger = get_logger(__name__)

async def run_full_check():
    checks = [
        mic_check.check,
        camera_check.check,
        nextion_check.check,
        bluetooth_check.check,
        audio_check.check,
        system_check.check,
    ]

    tasks = [asyncio.create_task(c()) for c in checks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    statuses = []
    for r, fn in zip(results, checks):
        module_name = getattr(fn, "__module__", "")
        name = module_name.split(".")[-1] if module_name else getattr(fn, "__name__", str(fn))
        if isinstance(r, Exception):
            logger.exception("Check %s raised", name)
            statuses.append({"name": name, "ok": False, "message": str(r)})
        else:
            statuses.append(r)
    return statuses

if __name__ == "__main__":
    # quick manual run
    import asyncio
    s = asyncio.run(run_full_check())
    print(s)
