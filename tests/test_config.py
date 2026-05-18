from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("BACKEND_", "RESPEAKER_", "BLUETOOTH_", "NEXTION_",
                           "ARDUINO_", "LISTEN_", "INTER_TURN_", "GESTURE_",
                           "ROBOT_ID", "LOG_LEVEL", "PULSE_SINK")):
            monkeypatch.delenv(key, raising=False)
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
    assert config.audio_output.bluetooth_mac == "CB:7A:DB:AD:30:D9"


def test_env_override(monkeypatch):
    monkeypatch.setenv("ROBOT_ID", "koda-02")
    monkeypatch.setenv("BACKEND_URL", "http://10.0.0.5:9000")
    monkeypatch.setenv("RESPEAKER_SAMPLE_RATE", "48000")
    monkeypatch.setenv("BLUETOOTH_AUTO_CONNECT", "0")
    monkeypatch.setenv("ARDUINO_PORT", "/dev/ttyACM0")

    config = _reload_config().load_config()
    assert config.robot_id == "koda-02"
    assert config.backend.base_url == "http://10.0.0.5:9000"
    assert config.respeaker.sample_rate == 48000
    assert config.audio_output.auto_connect is False
    assert config.arduino.port == "/dev/ttyACM0"
