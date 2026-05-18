from __future__ import annotations

from typing import Optional

from adapters.audio_output_adapter import AudioOutputAdapter
from adapters.backend_client import BackendClient
from utils.logger import get_logger

logger = get_logger(__name__)


class SpeechService:
    """Synthesize text or play pre-rendered WAV buffers through the audio output."""

    def __init__(
        self,
        backend: BackendClient,
        output: AudioOutputAdapter,
        default_voice: Optional[str] = None,
    ) -> None:
        self._backend = backend
        self._output = output
        self._default_voice = default_voice

    async def speak(self, text: str, voice: Optional[str] = None) -> None:
        if not text or not text.strip():
            logger.debug("speak() called with empty text; skipping")
            return
        wav = await self._backend.text_to_speech(text, voice or self._default_voice)
        await self._output.play_wav_bytes(wav)

    async def play_wav(self, wav: bytes) -> None:
        await self._output.play_wav_bytes(wav)
