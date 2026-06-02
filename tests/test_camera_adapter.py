from __future__ import annotations

from adapters.camera_adapter import CameraAdapter


def test_extract_first_jpeg_from_mjpeg_payload() -> None:
    frame = b"\xff\xd8hello-jpeg\xff\xd9"
    payload = b"noise-before" + frame + b"noise-after"

    assert CameraAdapter._extract_first_jpeg(payload) == frame


def test_extract_first_jpeg_returns_none_for_incomplete_frame() -> None:
    assert CameraAdapter._extract_first_jpeg(b"\xff\xd8partial") is None
