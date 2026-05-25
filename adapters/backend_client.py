from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Optional

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ActionResult:
    """Structured response from POST /api/audio/speech-to-action.

    `action` drives what the conversation service does next:
      - "text"   : just play `audio_wav` (TTS of the assistant reply)
      - "music"  : play `audio_wav` (announcement) THEN download `music_url` and play it
      - "motion" : play `audio_wav` (acknowledgment) THEN send `motion_command` to Arduino
      - "sleep"  : play `audio_wav` then transition robot to passive/sleep mode
      - "error"  : backend or n8n failed — `spoken_text` carries the error message
    """

    action: str
    input_text: str
    spoken_text: str
    audio_wav: bytes
    music_url: Optional[str] = None
    music_title: Optional[str] = None
    motion_command: Optional[str] = None

    @classmethod
    def from_json(cls, payload: dict) -> "ActionResult":
        audio_b64 = payload.get("audio_b64") or ""
        audio_wav = base64.b64decode(audio_b64) if audio_b64 else b""
        return cls(
            action=str(payload.get("action") or "text"),
            input_text=str(payload.get("input_text") or ""),
            spoken_text=str(payload.get("spoken_text") or ""),
            audio_wav=audio_wav,
            music_url=payload.get("music_url") or None,
            music_title=payload.get("music_title") or None,
            motion_command=payload.get("motion_command") or None,
        )


class BackendClient:
    """Async HTTP client for the FastAPI Distributeur (voice pipeline, STT/TTS, n8n)."""

    def __init__(self, base_url: str, timeout_seconds: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                http2=False,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "BackendClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def _require(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("BackendClient not started; call await client.start() first")
        return self._client

    async def health(self) -> bool:
        client = self._require()
        try:
            response = await client.get("/", timeout=15.0)
            return response.status_code < 500
        except httpx.HTTPError as exc:
            logger.debug("Backend health probe failed: %s", exc)
            return False

    async def text_to_speech(self, text: str, voice_name: Optional[str] = None) -> bytes:
        client = self._require()
        payload: dict[str, str] = {"text": text}
        if voice_name:
            payload["voice_name"] = voice_name
        response = await client.post("/api/audio/text-to-speech", json=payload)
        response.raise_for_status()
        return response.content

    async def speech_to_text(self, wav_bytes: bytes) -> str:
        client = self._require()
        logger.info("→ POST /api/audio/speech-to-text  wav=%d bytes", len(wav_bytes))
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        response = await client.post("/api/audio/speech-to-text", files=files)
        logger.info(
            "← STT response status=%d type=%s body=%s",
            response.status_code,
            response.headers.get("content-type", "?"),
            response.text[:300],
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return str(data.get("text") or "").strip()
        return ""

    async def speech_to_n8n_to_speech(
        self,
        wav_bytes: bytes,
        *,
        extra_text: Optional[str] = None,
        voice_name: Optional[str] = None,
    ) -> bytes:
        client = self._require()
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data: dict[str, str] = {}
        if extra_text:
            data["extra_text"] = extra_text
        if voice_name:
            data["voice_name"] = voice_name
        logger.info(
            "→ POST /api/audio/speech-to-n8n-to-speech  wav=%d bytes  form=%s",
            len(wav_bytes), data or "{}",
        )
        response = await client.post(
            "/api/audio/speech-to-n8n-to-speech",
            files=files,
            data=data,
        )
        if response.status_code >= 400:
            logger.error(
                "← pipeline error  status=%d  body=%s",
                response.status_code, response.text[:500],
            )
        else:
            logger.info(
                "← pipeline OK  status=%d  type=%s  size=%d bytes",
                response.status_code,
                response.headers.get("content-type", "?"),
                len(response.content),
            )
        response.raise_for_status()
        return response.content

    async def speech_to_action(
        self,
        wav_bytes: bytes,
        *,
        extra_text: Optional[str] = None,
        voice_name: Optional[str] = None,
    ) -> ActionResult:
        """Multi-type pipeline — preferred over speech_to_n8n_to_speech for full action support."""
        client = self._require()
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data: dict[str, str] = {}
        if extra_text:
            data["extra_text"] = extra_text
        if voice_name:
            data["voice_name"] = voice_name
        logger.info(
            "→ POST /api/audio/speech-to-action  wav=%d bytes  form=%s",
            len(wav_bytes), data or "{}",
        )
        response = await client.post(
            "/api/audio/speech-to-action",
            files=files,
            data=data,
        )
        if response.status_code >= 400:
            logger.error(
                "← action pipeline error  status=%d  body=%s",
                response.status_code, response.text[:500],
            )
        response.raise_for_status()
        payload = response.json()
        result = ActionResult.from_json(payload)
        logger.info(
            "← action=%s  input=%r  command=%r  url=%r  audio=%d bytes",
            result.action,
            result.input_text[:120],
            result.motion_command,
            result.music_url,
            len(result.audio_wav),
        )
        return result

    async def identify_face(self, jpg_bytes: bytes) -> str:
        """POST a JPEG to /api/identify-face and return the matched name.

        Returns "inconnu" on any error so the conversation never blocks because
        the camera or backend hiccupped.
        """
        if not jpg_bytes:
            return "inconnu"
        client = self._require()
        files = {"file": ("capture.jpg", jpg_bytes, "image/jpeg")}
        try:
            response = await client.post("/api/identify-face", files=files, timeout=15.0)
        except httpx.HTTPError as exc:
            logger.warning("identify_face network error: %s", exc)
            return "inconnu"
        if response.status_code != 200:
            logger.warning(
                "identify_face HTTP %d: %s",
                response.status_code, response.text[:200],
            )
            return "inconnu"
        try:
            body = response.json()
        except ValueError:
            return "inconnu"
        name = (body.get("nom") if isinstance(body, dict) else None) or "inconnu"
        return str(name).strip() or "inconnu"

    async def trigger_n8n(self, message: str) -> dict:
        client = self._require()
        logger.info("→ POST /api/trigger-n8n  message=%r", message[:300])
        response = await client.post("/api/trigger-n8n", json={"message": message})
        logger.info(
            "← n8n response status=%d type=%s body=%s",
            response.status_code,
            response.headers.get("content-type", "?"),
            response.text[:500],
        )
        response.raise_for_status()
        return response.json()
