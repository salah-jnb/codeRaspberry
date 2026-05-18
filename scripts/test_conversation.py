"""Run a single end-to-end conversation turn and exit.

Records `LISTEN_SECONDS` of audio, sends it to /api/audio/speech-to-n8n-to-speech,
plays the reply, and triggers the hello gesture in parallel.

Run:
    python -m scripts.test_conversation
    python -m scripts.test_conversation --seconds 4 --no-gesture
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.arduino_adapter import ArduinoAdapter
from adapters.audio_output_adapter import AudioOutputAdapter
from adapters.backend_client import BackendClient
from adapters.nextion_adapter import NextionAdapter
from adapters.respeaker_adapter import RespeakerAdapter
from app.config import load_config
from services.audio.audio_service import AudioService
from services.conversation.conversation_service import ConversationService
from services.display.display_service import DisplayService
from services.motion.motion_service import MotionService
from services.speech.speech_service import SpeechService
from utils.logger import get_logger

logger = get_logger("test_conversation")


async def main(seconds: float, gesture: bool) -> int:
    config = load_config()

    nextion = NextionAdapter(
        port=config.nextion.port,
        baudrate=config.nextion.baudrate,
        timeout=config.nextion.timeout_seconds,
    )
    arduino = ArduinoAdapter(
        port=config.arduino.port,
        baudrate=config.arduino.baudrate,
        timeout=config.arduino.timeout_seconds,
        boot_delay_seconds=config.arduino.boot_delay_seconds,
    )
    respeaker = RespeakerAdapter(
        alsa_device=config.respeaker.alsa_device,
        sample_rate=config.respeaker.sample_rate,
        channels=config.respeaker.channels,
        sample_format=config.respeaker.sample_format,
    )
    audio_output = AudioOutputAdapter(
        bluetooth_mac=config.audio_output.bluetooth_mac,
        pulse_sink=config.audio_output.pulse_sink,
    )
    backend = BackendClient(config.backend.base_url, config.backend.timeout_seconds)

    for label, opener in (("Nextion", nextion.open), ("Arduino", arduino.open)):
        try:
            opener()
            logger.info("%s opened", label)
        except RuntimeError as exc:
            logger.warning("%s unavailable: %s", label, exc)

    await backend.start()
    try:
        display = DisplayService(nextion)
        motion = MotionService(arduino)
        audio = AudioService(respeaker, seconds)
        speech = SpeechService(backend, audio_output, config.backend.voice_name)
        conversation = ConversationService(
            audio=audio,
            display=display,
            motion=motion,
            speech=speech,
            backend=backend,
            voice_name=config.backend.voice_name,
            extra_text=config.backend.extra_text,
            gesture_during_speech=gesture,
        )

        if nextion.is_open:
            await display.resume_idle()
        logger.info("Speak now (you have %.1fs) ...", seconds)
        await conversation.run_turn(seconds)
        logger.info("Conversation turn finished")
    finally:
        await backend.close()
        nextion.close()
        arduino.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--no-gesture", action="store_true", help="Do not move servos during playback")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.seconds, not args.no_gesture)))
