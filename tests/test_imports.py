"""Smoke test: every module imports without side-effects."""
from __future__ import annotations

import importlib

MODULES = (
    "app.config",
    "app.main",
    "adapters.arduino_adapter",
    "adapters.audio_output_adapter",
    "adapters.backend_client",
    "adapters.nextion_adapter",
    "adapters.respeaker_adapter",
    "core.event_bus",
    "services.audio.audio_service",
    "services.conversation.conversation_service",
    "services.display.display_service",
    "services.motion.motion_service",
    "services.speech.speech_service",
    "services.hardware_check.hardware_check_service",
    "services.wake_word.vosk_engine",
    "services.wake_word.wake_word_service",
    "scripts.download_vosk_model",
    "utils.logger",
)


def test_modules_import_cleanly():
    for mod in MODULES:
        importlib.import_module(mod)
