from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path
from typing import Optional

from adapters.audio_output_adapter import AudioOutputAdapter
from utils.logger import get_logger

logger = get_logger(__name__)


class MusicPlayer:
    """Download YouTube (or other yt-dlp-supported) audio and play it on the robot speaker.

    Cached WAV files live under `cache_dir/<sha1_of_url>.wav` so repeated requests
    for the same song skip the download entirely. The cache is bounded by
    `max_cache_files` (LRU eviction by atime) to avoid filling the SD card.
    """

    def __init__(
        self,
        output: AudioOutputAdapter,
        cache_dir: Path,
        *,
        max_cache_files: int = 25,
        download_timeout_seconds: float = 90.0,
    ) -> None:
        self._output = output
        self._cache_dir = cache_dir
        self._max_cache_files = max_cache_files
        self._download_timeout = download_timeout_seconds
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._download_lock = asyncio.Lock()

    def _cache_path_for(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        return self._cache_dir / f"{digest}.wav"

    async def play_from_url(self, url: str, *, title: Optional[str] = None) -> None:
        if not url or not str(url).strip():
            raise ValueError("MusicPlayer.play_from_url called with empty url")
        url = str(url).strip()

        if not shutil.which("yt-dlp"):
            raise RuntimeError(
                "yt-dlp not installed on this Pi. Install with: sudo apt install yt-dlp ffmpeg"
            )

        cache_path = self._cache_path_for(url)
        if cache_path.exists() and cache_path.stat().st_size > 0:
            logger.info("🎵 Music cache hit: %s (%r)", cache_path.name, title or url)
        else:
            async with self._download_lock:
                # Re-check after acquiring the lock in case another caller filled it.
                if not (cache_path.exists() and cache_path.stat().st_size > 0):
                    logger.info("🎵 Downloading audio: %r → %s", title or url, cache_path.name)
                    await self._download(url, cache_path)
                    self._evict_old_files()

        # Touch the file so LRU eviction keeps frequently-played tracks alive.
        try:
            cache_path.touch()
        except OSError:
            pass

        await self._output.play_wav_file(cache_path)

    async def _download(self, url: str, dest_wav: Path) -> None:
        # yt-dlp will append `.wav` to the template — pass it without extension.
        out_template = str(dest_wav.with_suffix("")) + ".%(ext)s"
        cmd = [
            "yt-dlp",
            "--quiet",
            "--no-warnings",
            "--no-playlist",
            "--extract-audio",
            "--audio-format", "wav",
            "--audio-quality", "0",
            "-o", out_template,
            url,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._download_timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"yt-dlp download timed out after {self._download_timeout:.0f}s for {url!r}"
            )
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"yt-dlp failed (code {proc.returncode}): {err[:500]}")
        if not dest_wav.exists() or dest_wav.stat().st_size == 0:
            raise RuntimeError(
                f"yt-dlp returned success but cache file {dest_wav} is missing/empty"
            )

    def _evict_old_files(self) -> None:
        files = sorted(
            (p for p in self._cache_dir.glob("*.wav") if p.is_file()),
            key=lambda p: p.stat().st_atime,
        )
        # Keep the N most-recently-used files; drop the rest.
        for path in files[: max(0, len(files) - self._max_cache_files)]:
            try:
                path.unlink()
                logger.debug("Evicted %s from music cache", path.name)
            except OSError:
                pass
