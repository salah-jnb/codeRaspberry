from __future__ import annotations

import asyncio
from typing import Optional

from adapters.backend_client import BackendClient
from services.audio.audio_service import AudioService
from services.display.display_service import DisplayService, Expression
from services.motion.motion_service import MotionService
from services.speech.speech_service import SpeechService
from utils.logger import get_logger

logger = get_logger(__name__)


class ConversationService:
    """Orchestrates one full vocal turn: record -> backend pipeline -> face/voice/gesture."""

    def __init__(
        self,
        audio: AudioService,
        display: DisplayService,
        motion: MotionService,
        speech: SpeechService,
        backend: BackendClient,
        *,
        voice_name: Optional[str] = None,
        extra_text: Optional[str] = None,
        gesture_during_speech: bool = True,
    ) -> None:
        self._audio = audio
        self._display = display
        self._motion = motion
        self._speech = speech
        self._backend = backend
        self._voice_name = voice_name
        self._extra_text = extra_text
        self._gesture_during_speech = gesture_during_speech

    async def run_turn(self, listen_seconds: Optional[float] = None) -> None:
        try:
            wav_in = await self._audio.record(listen_seconds)
        except Exception:
            logger.exception("Recording failed")
            await self._flash_expression(Expression.SAD)
            return

        await self._display.set_expression(Expression.THINKING)

        try:
            wav_out = await self._backend.speech_to_n8n_to_speech(
                wav_in,
                extra_text=self._extra_text,
                voice_name=self._voice_name,
            )
        except Exception:
            logger.exception("Backend pipeline failed")
            await self._flash_expression(Expression.SAD)
            return

        await self._display.set_expression(Expression.SINGING)
        try:
            await self._play_with_optional_gesture(wav_out)
        except Exception:
            logger.exception("Playback failed")
            await self._flash_expression(Expression.SAD)
            return
        finally:
            await self._display.resume_idle()

    async def _play_with_optional_gesture(self, wav: bytes) -> None:
        if not self._gesture_during_speech:
            await self._speech.play_wav(wav)
            return
        await asyncio.gather(
            self._speech.play_wav(wav),
            self._motion.hello(),
            return_exceptions=False,
        )

    async def _flash_expression(self, expression: Expression, hold_seconds: float = 1.5) -> None:
        await self._display.set_expression(expression)
        await asyncio.sleep(hold_seconds)
        await self._display.resume_idle()
