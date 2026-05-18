from __future__ import annotations

from typing import Optional

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)


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
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        response = await client.post("/api/audio/speech-to-text", files=files)
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
        response = await client.post(
            "/api/audio/speech-to-n8n-to-speech",
            files=files,
            data=data,
        )
        response.raise_for_status()
        return response.content

    async def trigger_n8n(self, message: str) -> dict:
        client = self._require()
        response = await client.post("/api/trigger-n8n", json={"message": message})
        response.raise_for_status()
        return response.json()
