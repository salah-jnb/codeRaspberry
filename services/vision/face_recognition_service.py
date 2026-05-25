from __future__ import annotations

import asyncio
import time
from typing import Optional

from adapters.backend_client import BackendClient
from adapters.camera_adapter import CameraAdapter
from utils.logger import get_logger

logger = get_logger(__name__)


_UNKNOWN_LABEL = "inconnu"


class FaceRecognitionService:
    """Capture a frame, identify the person via the backend, cache the result.

    Caching is per-conversation: at each wake-word the cache is refreshed
    (via `refresh()`), and follow-up questions reuse the same identity until
    either the cache expires or `refresh()` is called again. This avoids
    making one camera capture + one HTTP roundtrip per question.

    The service degrades gracefully:
      - If camera capture fails → returns `fallback_name` (or "inconnu").
      - If the backend rejects/times out → same.
      - The caller's pipeline keeps working in all cases.
    """

    def __init__(
        self,
        camera: CameraAdapter,
        backend: BackendClient,
        cache_seconds: float = 60.0,
        fallback_name: Optional[str] = None,
    ) -> None:
        self._camera = camera
        self._backend = backend
        self._cache_seconds = max(0.0, cache_seconds)
        self._fallback = (fallback_name or _UNKNOWN_LABEL).strip() or _UNKNOWN_LABEL
        self._cached_name: Optional[str] = None
        self._cached_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def cached_name(self) -> Optional[str]:
        if self._cached_name is None:
            return None
        if self._cache_seconds <= 0:
            return self._cached_name
        if (time.time() - self._cached_at) > self._cache_seconds:
            return None
        return self._cached_name

    async def refresh(self) -> str:
        """Force a new capture + identification. Updates the cache. Always returns a name."""
        async with self._lock:
            jpg = await self._camera.capture_jpeg()
            if not jpg:
                logger.warning("Face recognition: no image captured — using fallback %r", self._fallback)
                self._cached_name = self._fallback
                self._cached_at = time.time()
                return self._cached_name
            name = await self._backend.identify_face(jpg)
            if not name or name.lower() == _UNKNOWN_LABEL.lower():
                logger.info("👤 Face recognised: inconnu (no DB match)")
                name = _UNKNOWN_LABEL
            else:
                logger.info("👤 Face recognised: %r", name)
            self._cached_name = name
            self._cached_at = time.time()
            return name

    async def identify(self) -> str:
        """Return the cached name if still valid, otherwise capture + identify."""
        cached = self.cached_name
        if cached is not None:
            return cached
        return await self.refresh()

    def fire_and_forget_refresh(self) -> None:
        """Trigger a refresh in the background.

        Useful at wake-word time so identification runs in parallel with the
        rotation + greeting; the result is then available by the time the
        first question is sent to the backend.
        """
        try:
            asyncio.get_running_loop().create_task(self._safe_refresh())
        except RuntimeError:
            logger.debug("fire_and_forget_refresh called outside an event loop — ignored")

    async def _safe_refresh(self) -> None:
        try:
            await self.refresh()
        except Exception:
            logger.exception("Background face refresh failed")
