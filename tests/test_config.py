from __future__ import annotations

import importlib
import os

import pytest


_PREFIXES = (
    "BACKEND_", "RESPEAKER_", "BLUETOOTH_", "NEXTION_", "ARDUINO_",
    "LISTEN_", "INTER_TURN_", "GESTURE_", "WAKE_WORD_", "LISTENER_",
    "MAX_ACTIVE_", "ROBOT_ID", "LOG_LEVEL", "PULSE_SINK", "CAMERA_", "FACE_",
    "TOUCH_", "HYBRID_", "VOSK_",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith(_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    # Neutralize load_dotenv during reload so .env on disk does not leak into the test.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    yield


def _reload_config():
    import app.config as config
    return importlib.reload(config)


def test_defaults_are_sane():
    config = _reload_config().load_config()
    assert config.robot_id == "koda-01"
    assert config.backend.base_url.startswith("http://")
    assert config.respeaker.alsa_device == "plughw:3,0"
    assert config.respeaker.sample_rate == 16000
    assert config.nextion.port == "/dev/serial0"
    assert config.audio_output.bluetooth_mac == "AD:1C:99:E7:9B:78"
    assert config.wake_word.enabled is True
    assert config.listener.silence_duration_seconds == 1.5
    assert config.camera.prefer_mjpeg is True
    assert config.touch.enabled is True
    assert config.touch.pin == 17


def test_env_override(monkeypatch):
    monkeypatch.setenv("ROBOT_ID", "koda-02")
    monkeypatch.setenv("BACKEND_URL", "http://10.0.0.5:9000")
    monkeypatch.setenv("RESPEAKER_SAMPLE_RATE", "48000")
    monkeypatch.setenv("BLUETOOTH_AUTO_CONNECT", "0")
    monkeypatch.setenv("ARDUINO_PORT", "/dev/ttyACM0")
    monkeypatch.setenv("WAKE_WORD_ENABLED", "0")
    monkeypatch.setenv("FACE_API_BASE", "http://10.0.0.5:8765")
    monkeypatch.setenv("CAMERA_MJPEG_URL", "http://127.0.0.1:5000/video_feed")
    monkeypatch.setenv("TOUCH_SENSOR_ENABLED", "0")
    monkeypatch.setenv("TOUCH_SENSOR_PINS", "17,27")
    monkeypatch.setenv("TOUCH_LAUGH_WAV", "/tmp/laugh.wav")
    monkeypatch.setenv("WAKE_WORD_KEYWORDS", "koda, مرحبا , hey ")

    config = _reload_config().load_config()
    assert config.robot_id == "koda-02"
    assert config.backend.base_url == "http://10.0.0.5:9000"
    assert config.respeaker.sample_rate == 48000
    assert config.audio_output.auto_connect is False
    assert config.arduino.port == "/dev/ttyACM0"
    assert config.wake_word.enabled is False
    assert config.face_recognition.api_base_url == "http://10.0.0.5:8765"
    assert config.camera.mjpeg_url == "http://127.0.0.1:5000/video_feed"
    assert config.touch.enabled is False
    assert config.touch.pins == (17, 27)
    assert config.touch.pin == 17
    assert config.touch.laugh_wav_path == "/tmp/laugh.wav"
    assert config.wake_word.keywords == ("koda", "مرحبا", "hey")
