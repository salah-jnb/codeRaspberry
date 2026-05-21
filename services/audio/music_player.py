from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from adapters.audio_output_adapter import AudioOutputAdapter
from utils.logger import get_logger

logger = get_logger(__name__)


class MusicPlayer:
    """Download a music WAV (served by the backend) and play it on the robot speaker.

    Architecture: the backend on the PC runs yt-dlp (the Pi at e.g. ISET WiFi often
    has no public Internet), produces a cached `.wav`, and exposes it at a LAN URL.
    The Pi receives that URL via the `/api/audio/speech-to-action` response and
    just does an HTTP GET → cache locally → play via the existing audio output.

    The local cache here is a small bonus (e.g. for "rejoue la dernière chanson"
    follow-ups). It's keyed off the LAN URL path; same URL → same file.
    """

    def __init__(
        self,
        output: AudioOutputAdapter,
        cache_dir: Path,
        backend_base_url: str,
        *,
        max_cache_files: int = 25,
        download_timeout_seconds: float = 60.0,
    ) -> None:
        self._output = output
        self._cache_dir = cache_dir
        self._backend_base_url = backend_base_url.rstrip("/")
        self._max_cache_files = max_cache_files
        self._download_timeout = download_timeout_seconds
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._download_lock = asyncio.Lock()

    def _cache_path_for(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        return self._cache_dir / f"{digest}.wav"

    def _resolve_url(self, url: str) -> str:
        """Absolute URLs are kept; backend-relative URLs (`/cache/music/...`) get the
        backend base prefix so we don't depend on the Pi resolving the backend host
        the same way for every request."""
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            return url
        if url.startswith("/"):
            return f"{self._backend_base_url}{url}"
        return f"{self._backend_base_url}/{url}"

    async def play_from_url(self, url: str, *, title: Optional[str] = None) -> None:
        if not url or not str(url).strip():
            raise ValueError("MusicPlayer.play_from_url called with empty url")
        resolved = self._resolve_url(str(url).strip())
        cache_path = self._cache_path_for(resolved)

        if cache_path.exists() and cache_path.stat().st_size > 0:
            logger.info("🎵 Music cache hit: %s (%r)", cache_path.name, title or resolved)
        else:
            async with self._download_lock:
                if not (cache_path.exists() and cache_path.stat().st_size > 0):
                    logger.info("🎵 Fetching audio: %r → %s", title or resolved, cache_path.name)
                    await self._fetch(resolved, cache_path)
                    self._evict_old_files()

        try:
            cache_path.touch()
        except OSError:
            pass
        await self._output.play_wav_file(cache_path)

    async def _fetch(self, url: str, dest_wav: Path) -> None:
        tmp_path = dest_wav.with_suffix(".part")
        try:
            async with httpx.AsyncClient(timeout=self._download_timeout) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        body_preview = (await response.aread()).decode("utf-8", "replace")[:200]
                        raise RuntimeError(
                            f"Music fetch failed: HTTP {response.status_code} from {url} — {body_preview}"
                        )
                    with tmp_path.open("wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            f.write(chunk)
            if tmp_path.stat().st_size == 0:
                raise RuntimeError(f"Backend returned empty music payload for {url}")
            tmp_path.replace(dest_wav)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _evict_old_files(self) -> None:
        files = sorted(
            (p for p in self._cache_dir.glob("*.wav") if p.is_file()),
            key=lambda p: p.stat().st_atime,
        )
        for path in files[: max(0, len(files) - self._max_cache_files)]:
            try:
                path.unlink()
                logger.debug("Evicted %s from music cache", path.name)
            except OSError:
                pass
