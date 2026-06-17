from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from adapters.backend_client import ActionResult, BackendClient
from services.audio.audio_service import AudioService
from services.audio.music_player import MusicPlayer
from services.display.display_service import DisplayService, Expression
from services.listener.continuous_listener_service import ContinuousListenerService
from services.motion.motion_dispatcher import MotionDispatcher
from services.motion.motion_service import MotionService
from services.speech.speech_service import SpeechService
from services.vision.face_recognition_service import FaceRecognitionService
from utils.logger import get_logger

logger = get_logger(__name__)

_DEBUG_CAPTURE_WAV = Path("/tmp/koda_last_capture.wav")
_DEBUG_REPLY_WAV = Path("/tmp/koda_last_reply.wav")


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
        music_player: Optional[MusicPlayer] = None,
        motion_dispatcher: Optional[MotionDispatcher] = None,
        face_recognition: Optional[FaceRecognitionService] = None,
    ) -> None:
        self._audio = audio
        self._display = display
        self._motion = motion
        self._speech = speech
        self._backend = backend
        self._voice_name = voice_name
        self._extra_text = extra_text  # legacy fallback when face reco is disabled
        self._gesture_during_speech = gesture_during_speech
        self._listener = listener
        self._music_player = music_player
        self._face_recognition = face_recognition
        self._motion_dispatcher = motion_dispatcher or MotionDispatcher(motion)

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

    async def listen_for_followup(self, idle_timeout_s: float) -> bool:
        """Listen for a follow-up question, giving the user ``idle_timeout_s``
        to start talking. Returns True if speech was heard and answered, False
        if the user stayed silent (the caller then puts KODA back to sleep)."""
        if self._listener is None:
            return False
        try:
            wav_in = await self._listener.listen(no_speech_timeout_s=idle_timeout_s)
        except Exception:
            logger.exception("Follow-up listening failed")
            return False
        if not wav_in:
            return False  # silence within the idle window → sleep
        await self._process_audio(wav_in)
        return True

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

    async def passive_greet(self) -> None:
        """Démarrer une conversation après une longue période d'inactivité.

        Identifie le visage en face (ou ``"inconnu"``) puis appelle
        ``POST /api/webhook/nom`` côté backend, qui passe par le 2e workflow
        n8n et renvoie un ``ActionResult`` de type ``text`` (audio TTS
        embarqué). On le rejoue via le dispatcher existant.
        """
        name = "inconnu"
        if self._face_recognition is not None:
            try:
                identified = await self._face_recognition.identify()
                if identified and identified.strip():
                    name = identified.strip()
            except Exception:
                logger.exception("Face reco failed during passive greet — using 'inconnu'")

        logger.info("👋 Passive greet — appel /api/webhook/nom avec nom=%r", name)
        await self._display.set_expression(Expression.THINKING)
        try:
            result = await self._backend.greet_by_name(name, voice_name=self._voice_name)
        except Exception:
            logger.exception("Passive greet backend call failed — abandon")
            await self._flash_expression(Expression.SAD)
            return

        # On réutilise le dispatcher existant — `action` est typiquement `text`,
        # mais si n8n décide autre chose (musique d'ambiance par ex.) on gère.
        await self._dispatch_action(result)

    async def _resolve_extra_text(self) -> Optional[str]:
        """Decide what to put between parentheses after the STT result.

        Priority: live face recognition → legacy static `extra_text` → None.
        Returns "inconnu" rather than None when face reco is enabled so the
        backend's n8n always sees a `persone:` slot (the workflow's Code2
        node uses it for the prompt).
        """
        if self._face_recognition is not None:
            try:
                name = await self._face_recognition.identify()
                if name and name.strip():
                    return name.strip()
            except Exception:
                logger.exception("Face recognition failed — falling back to static extra_text")
        if self._extra_text and str(self._extra_text).strip():
            return str(self._extra_text).strip()
        if self._face_recognition is not None:
            return "inconnu"
        return self._extra_text

    async def _process_audio(self, wav_in: bytes) -> None:
        try:
            _DEBUG_CAPTURE_WAV.write_bytes(wav_in)
        except OSError as exc:
            logger.debug("Could not save debug capture WAV: %s", exc)

        extra_text = await self._resolve_extra_text()

        # Personne inconnue → on joint la photo capturée à la requête n8n, pour
        # que le workflow puisse enregistrer le nouveau visage si la question
        # est une présentation (« je m'appelle … »). Pour une personne connue,
        # inutile d'alourdir : on n'envoie rien.
        image_jpeg = None
        if self._face_recognition is not None and str(extra_text or "").strip().lower() in (
            "inconnu", "inconnue", "unknown", "unkonu",
        ):
            image_jpeg = self._face_recognition.cached_jpeg
            if image_jpeg:
                logger.info(
                    "🖼️  Personne inconnue — j'envoie la photo (%d octets) pour permettre l'enregistrement",
                    len(image_jpeg),
                )

        logger.info(
            "📥 Captured %d bytes (saved to %s)  extra_text=%r  voice=%r  image=%s",
            len(wav_in), _DEBUG_CAPTURE_WAV, extra_text, self._voice_name,
            f"{len(image_jpeg)}o" if image_jpeg else "non",
        )

        await self._display.set_expression(Expression.THINKING)

        try:
            result = await self._backend.speech_to_action(
                wav_in,
                extra_text=extra_text,
                voice_name=self._voice_name,
                image_jpeg=image_jpeg,
            )
        except Exception as exc:
            detail = getattr(getattr(exc, "response", None), "text", None)
            if detail:
                logger.error("Backend action pipeline failed: %s", detail.strip()[:500])
            else:
                logger.exception("Backend action pipeline failed")
            await self._flash_expression(Expression.SAD)
            return

        if result.input_text:
            logger.info("📝 Heard: %r", result.input_text[:300])

        try:
            if result.audio_wav:
                _DEBUG_REPLY_WAV.write_bytes(result.audio_wav)
        except OSError as exc:
            logger.debug("Could not save debug reply WAV: %s", exc)

        await self._dispatch_action(result)

    async def _dispatch_action(self, result: ActionResult) -> None:
        """Execute the action returned by the backend: text / music / motion / sleep / error."""
        action = (result.action or "text").lower()
        logger.info(
            "🎬 Dispatching action=%s  spoken=%r  command=%r  url=%r",
            action,
            result.spoken_text[:120] if result.spoken_text else "",
            result.motion_command,
            result.music_url,
        )

        try:
            if action == "music":
                await self._handle_music_action(result)
            elif action == "motion":
                await self._handle_motion_action(result)
            elif action == "sleep":
                await self._handle_sleep_action(result)
            elif action == "error":
                logger.error("Backend reported error action: %r", result.spoken_text)
                await self._flash_expression(Expression.SAD)
            else:
                # Default = "text" → just play the reply WAV (existing behavior).
                await self._handle_text_action(result)
        except Exception:
            logger.exception("Action dispatch failed (action=%s)", action)
            await self._flash_expression(Expression.SAD)
        finally:
            await self._display.resume_idle()

    async def _handle_text_action(self, result: ActionResult) -> None:
        if not result.audio_wav:
            logger.warning("text action with no audio WAV — nothing to play")
            return
        # Le visage reflète l'émotion de la réponse : le backend a retiré la
        # grimace « (émotion) » du texte et l'a normalisée en une catégorie
        # (happy/love/angry/sad/surprised). SINGING reste réservé à la musique.
        face = Expression.from_emotion(result.expression)
        logger.info("🙂 Réponse — visage=%s (emotion=%r)", face.name, result.expression)
        await self._display.set_expression(face)
        await self._play_wav_with_optional_gesture(result.audio_wav)

    async def _handle_music_action(self, result: ActionResult) -> None:
        if self._music_player is None:
            logger.error("Music action requested but no MusicPlayer was wired in")
            await self._flash_expression(Expression.SAD)
            return
        if not result.music_url:
            logger.warning("Music action with empty url — ignoring")
            return

        # 1. TTS announcement first (so the user hears "I'm playing X" before download wait).
        if result.audio_wav:
            await self._display.set_expression(Expression.SINGING)
            await self._speech.play_wav(result.audio_wav)

        # 2. Download (if not cached) + play. The MusicPlayer is responsible for caching.
        await self._display.set_expression(Expression.SINGING)
        await self._music_player.play_from_url(result.music_url, title=result.music_title)

    async def _handle_motion_action(self, result: ActionResult) -> None:
        # Announce first so the user gets feedback even while motors spin up.
        if result.audio_wav:
            await self._display.set_expression(Expression.SINGING)
            await self._speech.play_wav(result.audio_wav)
        await self._motion_dispatcher.execute(result.motion_command)

    async def _handle_sleep_action(self, result: ActionResult) -> None:
        # Play the goodbye line then drop the face back to idle. The caller
        # (main loop) handles re-entering passive mode after this returns.
        if result.audio_wav:
            await self._display.set_expression(Expression.SINGING)
            await self._speech.play_wav(result.audio_wav)
        await self._display.set_expression(Expression.SLEEPING)
        await asyncio.sleep(0.5)

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
