from __future__ import annotations

import asyncio
import signal
from typing import Awaitable, Callable, Optional

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
from services.listener.continuous_listener_service import (
    ContinuousListenerService,
    ListenerConfig,
)
from services.motion.motion_service import MotionService
from services.speech.speech_service import SpeechService
from services.wake_word.default_keywords import DEFAULT_KEYWORDS
from services.wake_word.wake_word_matcher import WakeWordMatcher
from services.wake_word.wake_word_service import WakeWordService
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


def _build_wake_word_service(
    config: AppConfig,
    audio: AudioService,
    backend: BackendClient,
) -> Optional[WakeWordService]:
    if not config.wake_word.enabled:
        return None
    keywords = config.wake_word.keywords or DEFAULT_KEYWORDS
    matcher = WakeWordMatcher(list(keywords))
    return WakeWordService(
        audio=audio,
        backend=backend,
        matcher=matcher,
        chunk_seconds=config.wake_word.chunk_seconds,
        cooldown_seconds=config.wake_word.cooldown_seconds,
    )


async def _run_wake_word_loop(
    wake_word: WakeWordService,
    conversation: ConversationService,
    display: DisplayService,
    motion: MotionService,
    config: AppConfig,
    stop_event: asyncio.Event,
) -> None:
    logger.info("KODA passive mode — waiting for wake word (keywords: %d variants)",
                len(wake_word._matcher.keywords))

    while not stop_event.is_set():
        match = await wake_word.wait_for_wake(stop_event)
        if match is None:
            return

        await _safe_async("display.set_expression(SURPRISED)",
                          display.set_expression(Expression.SURPRISED))
        if motion._adapter.is_open if hasattr(motion, "_adapter") else False:
            await _safe_async("motion.hello (ack)", motion.hello())

        if match.remainder:
            logger.info("Wake-word followed by text: %r", match.remainder)
            await conversation.handle_text_question(match.remainder)
        else:
            await _safe_async("display.set_expression(THINKING)",
                              display.set_expression(Expression.THINKING))
            await conversation.listen_and_answer()

        consecutive_silences = 0
        max_silences = max(0, config.conversation.max_active_silences)
        while not stop_event.is_set() and consecutive_silences < max_silences:
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=config.conversation.inter_turn_pause_seconds,
                )
                return
            except asyncio.TimeoutError:
                pass

            had_speech = await _run_active_turn(conversation)
            if had_speech:
                consecutive_silences = 0
            else:
                consecutive_silences += 1
                logger.info("Silence %d/%d in active mode", consecutive_silences, max_silences)

        logger.info("Returning to passive mode")
        await _safe_async("display.resume_idle", display.resume_idle())


async def _run_active_turn(conversation: ConversationService) -> bool:
    """Run one active conversation turn. Returns True if speech was heard."""
    try:
        await conversation.listen_and_answer()
        return True
    except Exception:
        logger.exception("Active turn failed")
        return False


async def _run_legacy_loop(
    conversation: ConversationService,
    config: AppConfig,
    stop_event: asyncio.Event,
) -> None:
    logger.info("KODA always-listening mode — wake word disabled")
    while not stop_event.is_set():
        await conversation.run_turn(config.conversation.listen_seconds)
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=config.conversation.inter_turn_pause_seconds,
            )
            return
        except asyncio.TimeoutError:
            continue


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

    listener = ContinuousListenerService(
        respeaker,
        ListenerConfig(
            max_seconds=config.listener.max_seconds,
            silence_duration_s=config.listener.silence_duration_seconds,
            silence_threshold_pct=config.listener.silence_threshold_pct,
            start_threshold_pct=config.listener.start_threshold_pct,
            min_speech_seconds=config.listener.min_speech_seconds,
        ),
    )

    conversation = ConversationService(
        audio=audio,
        display=display,
        motion=motion,
        speech=speech,
        backend=backend,
        voice_name=config.backend.voice_name,
        extra_text=config.backend.extra_text,
        gesture_during_speech=config.conversation.play_gesture_during_speech,
        listener=listener,
    )

    wake_word = _build_wake_word_service(config, audio, backend)

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

    try:
        if wake_word is not None:
            await _run_wake_word_loop(wake_word, conversation, display, motion, config, stop_event)
        else:
            await _run_legacy_loop(conversation, config, stop_event)
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
