"""Tests for the fire-and-forget refresh task tracking fix.

Before the fix, asyncio.create_task was called without keeping the reference,
so Python's GC could collect the task before it finished, raising
"Task was destroyed but it is pending!" warnings AND potentially leaking
camera capture state.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.vision.face_recognition_service import FaceRecognitionService


def _make_service() -> FaceRecognitionService:
    camera = MagicMock()
    camera.capture_jpeg = AsyncMock(return_value=b"\xff\xd8\xff\xd9")  # tiny JPEG header
    backend = MagicMock()
    backend.identify_face = AsyncMock(return_value="salah")
    return FaceRecognitionService(camera, backend, cache_seconds=60.0)


@pytest.mark.asyncio
async def test_refresh_task_is_tracked() -> None:
    """After fire_and_forget_refresh, the service must hold a strong ref to
    the task (otherwise GC + ResourceWarning)."""
    svc = _make_service()
    svc.fire_and_forget_refresh()
    assert svc._refresh_task is not None
    assert not svc._refresh_task.done()
    await svc._refresh_task  # let it complete
    # done_callback should clear the slot.
    await asyncio.sleep(0)  # let callback run
    assert svc._refresh_task is None


@pytest.mark.asyncio
async def test_second_refresh_is_skipped_while_in_flight() -> None:
    """Two near-simultaneous calls must NOT spawn two captures (camera race)."""
    svc = _make_service()

    # Make capture slow so we can call refresh twice in flight.
    slow_event = asyncio.Event()

    async def slow_capture():
        await slow_event.wait()
        return b"\xff\xd8\xff\xd9"

    svc._camera.capture_jpeg = slow_capture  # type: ignore[assignment]

    svc.fire_and_forget_refresh()
    first_task = svc._refresh_task
    svc.fire_and_forget_refresh()  # should be a no-op
    second_task = svc._refresh_task
    assert first_task is second_task, "second refresh leaked a new task"

    slow_event.set()
    await first_task


@pytest.mark.asyncio
async def test_cancel_pending_refresh_stops_orphan() -> None:
    """At shutdown, cancel_pending_refresh must terminate any in-flight refresh."""
    svc = _make_service()
    forever = asyncio.Event()

    async def hang_capture():
        await forever.wait()
        return b""

    svc._camera.capture_jpeg = hang_capture  # type: ignore[assignment]

    svc.fire_and_forget_refresh()
    task = svc._refresh_task
    assert task is not None
    await svc.cancel_pending_refresh()
    assert task.done()
    assert task.cancelled() or task.exception() is not None
