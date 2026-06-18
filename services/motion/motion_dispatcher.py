from __future__ import annotations

from typing import Optional

from adapters.arduino_adapter import ArduinoCommand
from services.motion.motion_service import MotionService
from utils.logger import get_logger

logger = get_logger(__name__)


class MotionDispatcher:
    """Traduit un ``motion_command`` du backend en gestes moteurs concrets.

    - ``forward`` / ``backward`` : marche pendant ``move_seconds`` puis arrêt
      automatique (la marche continue sinon indéfiniment jusqu'à un STOP).
    - ``left`` / ``right`` : rotation de ``turn_degrees`` en boucle fermée
      MPU6050 (``rotate_by_angle``), ou — en mode ``timed`` — une marche-virage
      de ``turn_seconds`` pour les firmwares sans gyroscope.
    - ``stop`` : arrêt immédiat.

    Garde les noms alignés avec ``_DIRECTION_WORD_TO_COMMAND`` du backend.
    """

    def __init__(
        self,
        motion: MotionService,
        *,
        move_seconds: float = 2.0,
        turn_mode: str = "angle",
        turn_degrees: float = 90.0,
        turn_seconds: float = 0.8,
    ) -> None:
        self._motion = motion
        self._move_seconds = max(0.0, float(move_seconds))
        self._turn_mode = (turn_mode or "angle").strip().lower()
        self._turn_degrees = abs(float(turn_degrees))
        self._turn_seconds = max(0.0, float(turn_seconds))

    async def execute(self, command: Optional[str]) -> bool:
        """Exécute la commande de mouvement. True si un ordre moteur a été émis."""
        if not command:
            logger.warning("MotionDispatcher.execute called with empty command")
            return False
        normalized = command.strip().lower()

        if normalized == "forward":
            logger.info("➡️  Motion: avancer %.2fs puis stop", self._move_seconds)
            await self._motion.move_for(ArduinoCommand.FORWARD, self._move_seconds)
        elif normalized == "backward":
            logger.info("➡️  Motion: reculer %.2fs puis stop", self._move_seconds)
            await self._motion.move_for(ArduinoCommand.BACKWARD, self._move_seconds)
        elif normalized == "left":
            await self._turn(sign=-1, byte=ArduinoCommand.LEFT, label="gauche")
        elif normalized == "right":
            await self._turn(sign=+1, byte=ArduinoCommand.RIGHT, label="droite")
        elif normalized == "stop":
            logger.info("➡️  Motion: stop")
            await self._motion.stop()
        else:
            logger.warning("Unknown motion command %r — ignoring", command)
            return False
        return True

    async def _turn(self, *, sign: int, byte: ArduinoCommand, label: str) -> None:
        """Tourne de 90° (par défaut). ``sign`` : -1 = gauche, +1 = droite
        (convention de ``rotate_by_angle`` : positif = droite/horaire)."""
        if self._turn_mode == "timed":
            logger.info("🧭 Motion: rotation %s ~%.2fs (mode temporisé)", label, self._turn_seconds)
            await self._motion.move_for(byte, self._turn_seconds)
        else:
            angle = sign * self._turn_degrees
            logger.info("🧭 Motion: rotation %s %+.0f° (boucle fermée MPU6050)", label, angle)
            await self._motion.rotate_by_angle(angle)
