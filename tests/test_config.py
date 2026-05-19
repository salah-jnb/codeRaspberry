from __future__ import annotations

import importlib
import os

import pytest


_PREFIXES = (
    "BACKEND_", "RESPEAKER_", "BLUETOOTH_", "NEXTION_", "ARDUINO_",
    "LISTEN_", "INTER_TURN_", "GESTURE_", "WAKE_WORD_", "LISTENER_",
    "MAX_ACTIVE_", "ROBOT_ID", "LOG_LEVEL", "PULSE_SINK",
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


def test_env_override(monkeypatch):
    monkeypatch.setenv("ROBOT_ID", "koda-02")
    monkeypatch.setenv("BACKEND_URL", "http://10.0.0.5:9000")
    monkeypatch.setenv("RESPEAKER_SAMPLE_RATE", "48000")
    monkeypatch.setenv("BLUETOOTH_AUTO_CONNECT", "0")
    monkeypatch.setenv("ARDUINO_PORT", "/dev/ttyACM0")
    monkeypatch.setenv("WAKE_WORD_ENABLED", "0")
    monkeypatch.setenv("WAKE_WORD_KEYWORDS", "koda, مرحبا , hey ")

    config = _reload_config().load_config()
    assert config.robot_id == "koda-02"
    assert config.backend.base_url == "http://10.0.0.5:9000"
    assert config.respeaker.sample_rate == 48000
    assert config.audio_output.auto_connect is False
    assert config.arduino.port == "/dev/ttyACM0"
    assert config.wake_word.enabled is False
    assert config.wake_word.keywords == ("koda", "مرحبا", "hey")
