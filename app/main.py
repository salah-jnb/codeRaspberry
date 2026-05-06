import asyncio
from app.config import load_config
from utils.logger import get_logger
from services.hardware_check.hardware_check_service import run_full_check

logger = get_logger(__name__)

COMPONENT_LABELS = {
    "mic_check": "Microphone (ReSpeaker)",
    "camera_check": "Camera",
    "nextion_check": "Nextion Display",
    "bluetooth_check": "Bluetooth HC-05",
    "audio_check": "Audio Output",
    "system_check": "System",
}


def _component_label(check_name: str) -> str:
    return COMPONENT_LABELS.get(check_name, check_name)


def _print_hardware_report(statuses):
    logger.info("Hardware detection report:")
    for status in statuses:
        label = _component_label(status.get("name", "unknown"))
        state = "DETECTED" if status.get("ok") else "NOT DETECTED"
        detail = status.get("message", "")
        logger.info(f" - {label:<22} | {state:<12} | {detail}")

async def main():
    config = load_config()
    logger.info("Koda Raspberry startup - config loaded")

    logger.info("Running hardware checks...")
    statuses = await run_full_check()

    ok = all(s.get("ok") for s in statuses)
    detected_count = sum(1 for s in statuses if s.get("ok"))
    total_count = len(statuses)

    _print_hardware_report(statuses)
    logger.info(f"Detected components: {detected_count}/{total_count}")

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
