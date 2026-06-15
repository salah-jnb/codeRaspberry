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
        # Dernière photo capturée lors de l'identification. Réutilisée par la
        # conversation pour l'envoyer à n8n quand la personne est inconnue (cas
        # « présentation » → enregistrement d'un nouveau visage), sans refaire
        # une capture caméra.
        self._cached_jpeg: Optional[bytes] = None
        self._cached_at: float = 0.0
        self._lock = asyncio.Lock()
        # Strong reference to the in-flight background refresh task. Without
        # this, asyncio's GC can collect the task before it finishes, which
        # raises "Task was destroyed but it is pending!" warnings AND can
        # leak partial state (capture process started, never closed).
        self._refresh_task: Optional[asyncio.Task] = None

    @property
    def cached_name(self) -> Optional[str]:
        if self._cached_name is None:
            return None
        if self._cache_seconds <= 0:
            return self._cached_name
        if (time.time() - self._cached_at) > self._cache_seconds:
            return None
        return self._cached_name

    @property
    def cached_jpeg(self) -> Optional[bytes]:
        """La dernière photo capturée, tant que le cache est valide (même
        fenêtre que ``cached_name``). ``None`` si rien ou cache expiré."""
        if self._cached_jpeg is None:
            return None
        if self._cache_seconds > 0 and (time.time() - self._cached_at) > self._cache_seconds:
            return None
        return self._cached_jpeg

    async def refresh(self) -> str:
        """Force a new capture + identification. Updates the cache. Always returns a name."""
        async with self._lock:
            jpg = await self._camera.capture_jpeg()
            self._cached_jpeg = jpg or None
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
        """Trigger a refresh in the background — at most one concurrent.

        Useful at wake-word time so identification runs in parallel with the
        rotation + greeting; the result is then available by the time the
        first question is sent to the backend.

        Guarantees:
          - The task reference is kept on ``self._refresh_task`` so GC can't
            destroy it (no more "Task destroyed but pending" warnings).
          - At most one refresh runs at a time. If one is in flight, this
            call is a no-op (we'd race the camera otherwise).
          - The task removes itself from the slot on completion.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("fire_and_forget_refresh called outside an event loop — ignored")
            return

        existing = self._refresh_task
        if existing is not None and not existing.done():
            logger.debug("face refresh already in flight — skipping new request")
            return

        task = loop.create_task(self._safe_refresh(), name="face_refresh")
        self._refresh_task = task
        task.add_done_callback(self._on_refresh_done)

    def _on_refresh_done(self, task: asyncio.Task) -> None:
        # Clear the slot only if it's still the same task (in case shutdown
        # already swapped it out).
        if self._refresh_task is task:
            self._refresh_task = None
        # Surface any unexpected error here so it doesn't disappear silently.
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.warning("face refresh background task ended with error: %r", exc)

    async def cancel_pending_refresh(self) -> None:
        """Called at shutdown to make sure no orphan refresh leaks a camera
        capture or HTTP socket."""
        task = self._refresh_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _safe_refresh(self) -> None:
        try:
            await self.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background face refresh failed")
