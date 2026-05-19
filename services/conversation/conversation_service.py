from __future__ import annotations

import asyncio
from typing import Optional

from adapters.backend_client import BackendClient
from services.audio.audio_service import AudioService
from services.display.display_service import DisplayService, Expression
from services.listener.continuous_listener_service import ContinuousListenerService
from services.motion.motion_service import MotionService
from services.speech.speech_service import SpeechService
from utils.logger import get_logger

logger = get_logger(__name__)


class ConversationService:
    """Orchestrates a vocal turn (audio capture or pre-captured text -> backend -> display + voice + gesture)."""

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
        listener: Optional[ContinuousListenerService] = None,
    ) -> None:
        self._audio = audio
        self._display = display
        self._motion = motion
        self._speech = speech
        self._backend = backend
        self._voice_name = voice_name
        self._extra_text = extra_text
        self._gesture_during_speech = gesture_during_speech
        self._listener = listener

    async def run_turn(self, listen_seconds: Optional[float] = None) -> None:
        """Record (fixed duration) → speech-to-n8n-to-speech → playback."""
        try:
            wav_in = await self._audio.record(listen_seconds)
        except Exception:
            logger.exception("Recording failed")
            await self._flash_expression(Expression.SAD)
            return
        await self._process_audio(wav_in)

    async def listen_and_answer(self) -> None:
        """Record (VAD until silence) → speech-to-n8n-to-speech → playback."""
        if self._listener is None:
            await self.run_turn()
            return
        try:
            wav_in = await self._listener.listen()
        except Exception:
            logger.exception("Continuous listening failed")
            await self._flash_expression(Expression.SAD)
            return
        await self._process_audio(wav_in)

    async def handle_text_question(self, text: str) -> None:
        """Send pre-transcribed text to n8n, synthesize, play the reply."""
        if not text or not text.strip():
            logger.debug("handle_text_question called with empty text; skipping")
            return

        message = text.strip()
        if self._extra_text:
            message = f"{message} ({self._extra_text})"

        await self._display.set_expression(Expression.THINKING)
        try:
            response = await self._backend.trigger_n8n(message)
            reply = self._extract_reply(response)
        except Exception as exc:
            detail = getattr(getattr(exc, "response", None), "text", None)
            if detail:
                logger.error("n8n trigger failed: %s", detail.strip()[:500])
            else:
                logger.exception("n8n trigger failed")
            await self._flash_expression(Expression.SAD)
            return

        if not reply:
            logger.warning("n8n returned no usable reply")
            await self._flash_expression(Expression.SAD)
            return

        await self._display.set_expression(Expression.SINGING)
        try:
            wav = await self._backend.text_to_speech(reply, self._voice_name)
            await self._play_wav_with_optional_gesture(wav)
        except Exception:
            logger.exception("TTS playback failed")
            await self._flash_expression(Expression.SAD)
            return
        finally:
            await self._display.resume_idle()

    async def _process_audio(self, wav_in: bytes) -> None:
        await self._display.set_expression(Expression.THINKING)
        try:
            wav_out = await self._backend.speech_to_n8n_to_speech(
                wav_in,
                extra_text=self._extra_text,
                voice_name=self._voice_name,
            )
        except Exception as exc:
            detail = getattr(getattr(exc, "response", None), "text", None)
            if detail:
                logger.error("Backend pipeline failed: %s", detail.strip()[:500])
            else:
                logger.exception("Backend pipeline failed")
            await self._flash_expression(Expression.SAD)
            return

        await self._display.set_expression(Expression.SINGING)
        try:
            await self._play_wav_with_optional_gesture(wav_out)
        except Exception:
            logger.exception("Playback failed")
            await self._flash_expression(Expression.SAD)
            return
        finally:
            await self._display.resume_idle()

    async def _play_wav_with_optional_gesture(self, wav: bytes) -> None:
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

    @staticmethod
    def _extract_reply(payload) -> str:
        """Extract the assistant reply from a variety of n8n response shapes."""
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, list):
            for item in payload:
                value = ConversationService._extract_reply(item)
                if value:
                    return value
            return ""
        if isinstance(payload, dict):
            for key in ("output", "reply", "response", "answer", "text", "message", "result"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in payload.values():
                extracted = ConversationService._extract_reply(value)
                if extracted:
                    return extracted
        return ""
