from __future__ import annotations

import asyncio
import signal
from typing import Awaitable, Callable

from adapters.arduino_adapter import ArduinoAdapter
from adapters.audio_output_adapter import AudioOutputAdapter
from adapters.backend_client import BackendClient
from adapters.nextion_adapter import NextionAdapter
from adapters.respeaker_adapter import RespeakerAdapter
from app.config import AppConfig, load_config
from services.audio.audio_service import AudioService
from services.conversation.conversation_service import ConversationService
from services.display.display_service import DisplayService, Expression
from services.hardware_check.hardware_check_service import run_full_check
from services.motion.motion_service import MotionService
from services.speech.speech_service import SpeechService
from utils.logger import get_logger

logger = get_logger(__name__)


_COMPONENT_LABELS = {
    "mic_check": "ReSpeaker (USB mic)",
    "camera_check": "Camera (CSI)",
    "nextion_check": "Nextion display",
    "arduino_check": "Arduino USB",
    "bluetooth_check": "Bluetooth speaker",
    "audio_check": "Audio output",
    "system_check": "System",
}


def _label(check_name: str) -> str:
    return _COMPONENT_LABELS.get(check_name, check_name)


async def _report_hardware() -> None:
    statuses = await run_full_check()
    detected = sum(1 for s in statuses if s.get("ok"))
    logger.info("Hardware detection report (%d/%d):", detected, len(statuses))
    for status in statuses:
        state = "DETECTED" if status.get("ok") else "MISSING "
        logger.info(
            " - %-22s | %s | %s",
            _label(status.get("name", "?")),
            state,
            status.get("message", ""),
        )


def _safe_open(label: str, opener: Callable[[], None]) -> bool:
    try:
        opener()
        logger.info("%s opened", label)
        return True
    except Exception as exc:
        logger.warning("%s unavailable: %s", label, exc)
        return False


async def _safe_async(label: str, awaitable: Awaitable) -> None:
    try:
        await awaitable
    except Exception:
        logger.exception("%s failed", label)


async def _shutdown(
    backend: BackendClient,
    nextion: NextionAdapter,
    arduino: ArduinoAdapter,
    display: DisplayService,
    motion: MotionService,
) -> None:
    logger.info("Shutting down KODA...")
    await _safe_async("display.set_expression(SLEEPING)", display.set_expression(Expression.SLEEPING))
    if arduino.is_open:
        await _safe_async("motion.stop", motion.stop())
    await _safe_async("backend.close", backend.close())
    try:
        nextion.close()
    except Exception:
        logger.exception("Nextion close failed")
    try:
        arduino.close()
    except Exception:
        logger.exception("Arduino close failed")
    logger.info("KODA stopped")


async def run(config: AppConfig) -> None:
    logger.info("KODA booting (robot_id=%s, backend=%s)", config.robot_id, config.backend.base_url)

    await _report_hardware()

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
        require_ack=config.arduino.require_ack,
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

    _safe_open("Nextion", nextion.open)
    _safe_open("Arduino", arduino.open)

    if config.audio_output.auto_connect:
        connected = await audio_output.ensure_bluetooth()
        if connected:
            logger.info("Bluetooth speaker ready: %s", config.audio_output.bluetooth_mac)
        else:
            logger.warning("Bluetooth speaker unavailable; falling back to default sink")

    backend = BackendClient(config.backend.base_url, config.backend.timeout_seconds)
    await backend.start()

    if not await backend.health():
        logger.warning("Backend health probe failed (continuing — may recover)")

    display = DisplayService(nextion)
    motion = MotionService(arduino)
    audio = AudioService(respeaker, config.respeaker.record_seconds)
    speech = SpeechService(backend, audio_output, config.backend.voice_name)
    conversation = ConversationService(
        audio=audio,
        display=display,
        motion=motion,
        speech=speech,
        backend=backend,
        voice_name=config.backend.voice_name,
        extra_text=config.backend.extra_text,
        gesture_during_speech=config.conversation.play_gesture_during_speech,
    )

    if nextion.is_open:
        await display.resume_idle()
    if arduino.is_open:
        await _safe_async("motion.hello (greeting)", motion.hello())

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info("KODA ready — entering conversation loop")
    try:
        while not stop_event.is_set():
            await conversation.run_turn(config.conversation.listen_seconds)
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=config.conversation.inter_turn_pause_seconds,
                )
                break
            except asyncio.TimeoutError:
                continue
    finally:
        await _shutdown(backend, nextion, arduino, display, motion)


def main() -> None:
    config = load_config()
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
