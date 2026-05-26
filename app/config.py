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
    base_url: str = "http://SALAH_DESKTOP.local:8765"
    timeout_seconds: float = 60.0
    voice_name: Optional[str] = None
    extra_text: Optional[str] = None


@dataclass(frozen=True)
class RespeakerConfig:
    alsa_device: str = "plughw:3,0"
    sample_rate: int = 16000
    channels: int = 1  # output channels delivered to consumers (Vosk / Azure STT) — keep at 1
    record_seconds: float = 5.0
    sample_format: str = "S16_LE"
    # Number of channels the ReSpeaker firmware exposes on USB BEFORE remix.
    # ReSpeaker 4-Mic Array v2.0 default firmware = 6 channels:
    #   ch0 = AEC+BF+NS+AGC processed mono, ch1-4 = raw mics, ch5 = playback ref.
    # If you flashed the 1-channel firmware, set this to 1 to skip the remix step.
    native_channels: int = 6
    # Which native channel to keep after remix (0-based). ch0 = processed audio.
    processed_channel_index: int = 0


@dataclass(frozen=True)
class AudioOutputConfig:
    bluetooth_mac: Optional[str] = "AD:1C:99:E7:9B:78"
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
    max_active_silences: int = 3
    greeting_text: str = "أهلا بيك"


@dataclass(frozen=True)
class WakeWordConfig:
    enabled: bool = True
    keywords: tuple[str, ...] = ()
    chunk_seconds: float = 2.0
    cooldown_seconds: float = 0.1
    # When True, also fetch dynamic name variants from the backend at boot.
    fetch_variants_from_backend: bool = False


@dataclass(frozen=True)
class VoskConfig:
    # Set to "" to fall back to the legacy Azure-STT chunk wake-word path.
    language: str = "ar"
    models_dir: str = "models/vosk"
    # Bytes per audio chunk fed to the recognizer (~250ms at 16k/16-bit/mono).
    chunk_bytes: int = 8000
    # Auto-download the model on first boot if it is missing locally.
    auto_download: bool = True


@dataclass(frozen=True)
class ListenerConfigEntry:
    max_seconds: float = 15.0
    silence_duration_seconds: float = 1.5
    silence_threshold_pct: float = 1.0
    start_threshold_pct: float = 1.0
    min_speech_seconds: float = 0.2


@dataclass(frozen=True)
class CameraConfig:
    width: int = 1280
    height: int = 720
    capture_timeout_ms: int = 500


@dataclass(frozen=True)
class FaceRecognitionConfig:
    enabled: bool = True
    # Reuse the identified name across follow-ups without re-capturing.
    cache_seconds: float = 60.0
    # Fallback when no face is detected / camera fails.
    fallback_name: str = "inconnu"


@dataclass(frozen=True)
class RotationConfig:
    """Open-loop rotation toward the speaker (DOA → motor pulse)."""
    enabled: bool = True
    # Linear timing model (used when `lut` is empty).
    slope_deg_per_s: float = 60.0
    offset_deg: float = 3.3
    min_duration_s: float = 0.05
    settle_s: float = 0.4
    deadband_deg: float = 5.0
    # ReSpeaker calibration:
    #   front_offset_deg = raw DOA value reported when a speaker is exactly in front
    #   invert_direction = True if mic 0° points backwards in your physical build
    front_offset_deg: float = 0.0
    invert_direction: bool = False
    # Optional lookup table: pairs "angle:duration" separated by commas, e.g.
    #   ROTATION_LUT=30:0.5,60:0.95,90:1.4,135:2.0,180:2.7
    # When set, this overrides the linear formula above (much more precise once
    # measured properly on the actual chassis + battery).
    lut: tuple[tuple[float, float], ...] = ()


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
    wake_word: WakeWordConfig = field(default_factory=WakeWordConfig)
    vosk: VoskConfig = field(default_factory=VoskConfig)
    listener: ListenerConfigEntry = field(default_factory=ListenerConfigEntry)
    rotation: RotationConfig = field(default_factory=RotationConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    face_recognition: FaceRecognitionConfig = field(default_factory=FaceRecognitionConfig)


def load_config() -> AppConfig:
    backend = BackendConfig(
        base_url=_env_str("BACKEND_URL", "http://SALAH_DESKTOP.local:8765"),
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
        native_channels=_env_int("RESPEAKER_NATIVE_CHANNELS", 6),
        processed_channel_index=_env_int("RESPEAKER_PROCESSED_CHANNEL_INDEX", 0),
    )
    audio_output = AudioOutputConfig(
        bluetooth_mac=_env_optional("BLUETOOTH_MAC") or "AD:1C:99:E7:9B:78",
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
        max_active_silences=_env_int("MAX_ACTIVE_SILENCES", 3),
        greeting_text=_env_str("GREETING_TEXT", "أهلا بيك"),
    )
    wake_word = WakeWordConfig(
        enabled=_env_str("WAKE_WORD_ENABLED", "1") not in {"0", "false", "no"},
        keywords=_parse_keywords(_env_optional("WAKE_WORD_KEYWORDS")),
        chunk_seconds=_env_float("WAKE_WORD_CHUNK_SECONDS", 2.0),
        cooldown_seconds=_env_float("WAKE_WORD_COOLDOWN", 0.1),
        fetch_variants_from_backend=_env_str("WAKE_WORD_FETCH_VARIANTS", "0") in {"1", "true", "yes"},
    )
    vosk = VoskConfig(
        language=_env_str("VOSK_LANGUAGE", "ar"),
        models_dir=_env_str("VOSK_MODELS_DIR", "models/vosk"),
        chunk_bytes=_env_int("VOSK_CHUNK_BYTES", 8000),
        auto_download=_env_str("VOSK_AUTO_DOWNLOAD", "1") not in {"0", "false", "no"},
    )
    listener = ListenerConfigEntry(
        max_seconds=_env_float("LISTENER_MAX_SECONDS", 15.0),
        silence_duration_seconds=_env_float("LISTENER_SILENCE_DURATION", 1.5),
        silence_threshold_pct=_env_float("LISTENER_SILENCE_THRESHOLD_PCT", 1.0),
        start_threshold_pct=_env_float("LISTENER_START_THRESHOLD_PCT", 1.0),
        min_speech_seconds=_env_float("LISTENER_MIN_SPEECH_SECONDS", 0.2),
    )
    rotation = RotationConfig(
        enabled=_env_str("ROTATION_ENABLED", "1") not in {"0", "false", "no"},
        slope_deg_per_s=_env_float("ROTATION_SLOPE_DEG_PER_S", 60.0),
        offset_deg=_env_float("ROTATION_OFFSET_DEG", 3.3),
        min_duration_s=_env_float("ROTATION_MIN_DURATION_S", 0.05),
        settle_s=_env_float("ROTATION_SETTLE_S", 0.4),
        deadband_deg=_env_float("ROTATION_DEADBAND_DEG", 5.0),
        front_offset_deg=_env_float("ROTATION_FRONT_OFFSET_DEG", 0.0),
        invert_direction=_env_str("ROTATION_INVERT_DIRECTION", "0") in {"1", "true", "yes"},
        lut=_parse_rotation_lut(_env_optional("ROTATION_LUT")),
    )
    camera = CameraConfig(
        width=_env_int("CAMERA_WIDTH", 1280),
        height=_env_int("CAMERA_HEIGHT", 720),
        capture_timeout_ms=_env_int("CAMERA_CAPTURE_TIMEOUT_MS", 500),
    )
    face_recognition = FaceRecognitionConfig(
        enabled=_env_str("FACE_RECOGNITION_ENABLED", "1") not in {"0", "false", "no"},
        cache_seconds=_env_float("FACE_RECOGNITION_CACHE_SECONDS", 60.0),
        fallback_name=_env_str("FACE_RECOGNITION_FALLBACK", "inconnu"),
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
        wake_word=wake_word,
        vosk=vosk,
        listener=listener,
        rotation=rotation,
        camera=camera,
        face_recognition=face_recognition,
    )


def _parse_rotation_lut(raw: Optional[str]) -> tuple[tuple[float, float], ...]:
    """Parse "30:0.5,60:0.95,90:1.4" into ((30,0.5),(60,0.95),(90,1.4))."""
    if not raw or not raw.strip():
        return ()
    pairs: list[tuple[float, float]] = []
    for chunk in raw.split(","):
        if ":" not in chunk:
            continue
        angle_str, duration_str = chunk.split(":", 1)
        try:
            pairs.append((float(angle_str.strip()), float(duration_str.strip())))
        except ValueError:
            continue
    return tuple(sorted(pairs))


def _parse_keywords(raw: Optional[str]) -> tuple[str, ...]:
    if not raw:
        return ()
    parts = [chunk.strip() for chunk in raw.split(",")]
    return tuple(p for p in parts if p)
