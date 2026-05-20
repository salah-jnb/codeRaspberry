from __future__ import annotations

from typing import Optional

from services.motion.motion_service import MotionService
from utils.logger import get_logger

logger = get_logger(__name__)


# Maps the backend's normalized `motion_command` to the MotionService method that
# actually sends the single-byte command to the Arduino. Keep these names aligned
# with `_DIRECTION_WORD_TO_COMMAND` in the backend's app_service_impl.py.
_COMMAND_TO_METHOD = {
    "forward": "forward",
    "backward": "backward",
    "left": "left",
    "right": "right",
    "stop": "stop",
}


class MotionDispatcher:
    """Bridges a backend-emitted motion_command string to a MotionService call."""

    def __init__(self, motion: MotionService) -> None:
        self._motion = motion

    async def execute(self, command: Optional[str]) -> bool:
        """Run the motion command. Returns True if a motor command was actually sent."""
        if not command:
            logger.warning("MotionDispatcher.execute called with empty command")
            return False
        normalized = command.strip().lower()
        method_name = _COMMAND_TO_METHOD.get(normalized)
        if method_name is None:
            logger.warning("Unknown motion command %r — ignoring", command)
            return False
        method = getattr(self._motion, method_name, None)
        if method is None:
            logger.error("MotionService has no method %r", method_name)
            return False
        logger.info("➡️  Motion command: %s", normalized)
        await method()
        return True
