from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_str(key: str, default: str) -> str:
    value = os.environ.get(key)
    return value.strip() if value and value.strip() else default


def _env_optional(key: str) -> Optional[str]:
    value = os.environ.get(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_int(key: str, default: int) -> int:
    value = os.environ.get(key)
    if not value or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    value = os.environ.get(key)
    if not value or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class BackendConfig:
    base_url: str = "http://192.168.100.82:8765"
    timeout_seconds: float = 60.0
    voice_name: Optional[str] = None
    extra_text: Optional[str] = None


@dataclass(frozen=True)
class RespeakerConfig:
    alsa_device: str = "plughw:3,0"
    sample_rate: int = 16000
    channels: int = 1
    record_seconds: float = 5.0
    sample_format: str = "S16_LE"


@dataclass(frozen=True)
class AudioOutputConfig:
    bluetooth_mac: Optional[str] = "CB:7A:DB:AD:30:D9"
    pulse_sink: Optional[str] = None
    auto_connect: bool = True


@dataclass(frozen=True)
class NextionConfig:
    port: str = "/dev/serial0"
    baudrate: int = 9600
    timeout_seconds: float = 1.0


@dataclass(frozen=True)
class ArduinoConfig:
    port: Optional[str] = None
    baudrate: int = 9600
    timeout_seconds: float = 2.0
    boot_delay_seconds: float = 2.0
    require_ack: bool = False


@dataclass(frozen=True)
class ConversationConfig:
    listen_seconds: float = 5.0
    inter_turn_pause_seconds: float = 0.5
    play_gesture_during_speech: bool = True


@dataclass(frozen=True)
class AppConfig:
    robot_id: str = "koda-01"
    log_level: str = "INFO"
    backend: BackendConfig = field(default_factory=BackendConfig)
    respeaker: RespeakerConfig = field(default_factory=RespeakerConfig)
    audio_output: AudioOutputConfig = field(default_factory=AudioOutputConfig)
    nextion: NextionConfig = field(default_factory=NextionConfig)
    arduino: ArduinoConfig = field(default_factory=ArduinoConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)


def load_config() -> AppConfig:
    backend = BackendConfig(
        base_url=_env_str("BACKEND_URL", "http://192.168.100.82:8765"),
        timeout_seconds=_env_float("BACKEND_TIMEOUT", 60.0),
        voice_name=_env_optional("BACKEND_VOICE_NAME"),
        extra_text=_env_optional("BACKEND_EXTRA_TEXT"),
    )
    respeaker = RespeakerConfig(
        alsa_device=_env_str("RESPEAKER_DEVICE", "plughw:3,0"),
        sample_rate=_env_int("RESPEAKER_SAMPLE_RATE", 16000),
        channels=_env_int("RESPEAKER_CHANNELS", 1),
        record_seconds=_env_float("RESPEAKER_RECORD_SECONDS", 5.0),
        sample_format=_env_str("RESPEAKER_SAMPLE_FORMAT", "S16_LE"),
    )
    audio_output = AudioOutputConfig(
        bluetooth_mac=_env_optional("BLUETOOTH_MAC") or "CB:7A:DB:AD:30:D9",
        pulse_sink=_env_optional("PULSE_SINK"),
        auto_connect=_env_str("BLUETOOTH_AUTO_CONNECT", "1") not in {"0", "false", "no"},
    )
    nextion = NextionConfig(
        port=_env_str("NEXTION_PORT", "/dev/serial0"),
        baudrate=_env_int("NEXTION_BAUDRATE", 9600),
        timeout_seconds=_env_float("NEXTION_TIMEOUT", 1.0),
    )
    arduino = ArduinoConfig(
        port=_env_optional("ARDUINO_PORT"),
        baudrate=_env_int("ARDUINO_BAUDRATE", 9600),
        timeout_seconds=_env_float("ARDUINO_TIMEOUT", 2.0),
        boot_delay_seconds=_env_float("ARDUINO_BOOT_DELAY", 2.0),
        require_ack=_env_str("ARDUINO_REQUIRE_ACK", "0") in {"1", "true", "yes"},
    )
    conversation = ConversationConfig(
        listen_seconds=_env_float("LISTEN_SECONDS", respeaker.record_seconds),
        inter_turn_pause_seconds=_env_float("INTER_TURN_PAUSE", 0.5),
        play_gesture_during_speech=_env_str("GESTURE_DURING_SPEECH", "1") not in {"0", "false", "no"},
    )
    return AppConfig(
        robot_id=_env_str("ROBOT_ID", "koda-01"),
        log_level=_env_str("LOG_LEVEL", "INFO"),
        backend=backend,
        respeaker=respeaker,
        audio_output=audio_output,
        nextion=nextion,
        arduino=arduino,
        conversation=conversation,
    )
