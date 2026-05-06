import asyncio
from app.config import load_config
from utils.logger import get_logger
from services.hardware_check.hardware_check_service import run_full_check

logger = get_logger(__name__)

async def main():
    config = load_config()
    logger.info("Koda Raspberry startup - config loaded")

    logger.info("Running hardware checks...")
    statuses = await run_full_check()

    ok = all(s.get("ok") for s in statuses)
    for s in statuses:
        logger.info(f"{s['name']}: {'OK' if s['ok'] else 'FAIL'} - {s.get('message')}" )

    if not ok:
        logger.warning("Hardware checks reported issues. Starting in degraded mode.")
    else:
        logger.info("All hardware checks passed.")

    # For now, we stop after hardware checks. Later: init services.

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
