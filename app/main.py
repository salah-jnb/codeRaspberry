from __future__ import annotations

import asyncio
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Awaitable, Callable, Optional

from adapters.arduino_adapter import ArduinoAdapter
from adapters.audio_output_adapter import AudioOutputAdapter
from adapters.backend_client import BackendClient
from adapters.camera_adapter import CameraAdapter
from adapters.nextion_adapter import NextionAdapter
from adapters.respeaker_adapter import RespeakerAdapter
from app.config import AppConfig, load_config
from services.audio.audio_service import AudioService
from services.audio.doa_reader import DOAReader
from services.audio.music_player import MusicPlayer
from services.conversation.conversation_service import ConversationService
from services.display.display_service import DisplayService, Expression
from services.hardware_check.hardware_check_service import run_full_check
from services.listener.continuous_listener_service import (
    ContinuousListenerService,
    ListenerConfig,
)
from services.motion.motion_service import (
    MotionService,
    RotationCalibration,
    shortest_signed_angle,
)
from services.speech.speech_service import SpeechService
from services.vision.face_recognition_service import FaceRecognitionService
from services.wake_word.default_keywords import DEFAULT_KEYWORDS
from services.wake_word.wake_word_matcher import WakeWordMatcher
from services.wake_word.wake_word_service import WakeWordService
from utils.logger import get_logger
from utils.states import error, state, warn

try:
    from services.wake_word.vosk_engine import VoskWakeWordEngine
except Exception:  # pragma: no cover — vosk not installed during early dev
    VoskWakeWordEngine = None  # type: ignore[assignment]

try:
    from services.wake_word.hybrid_wake_word_engine import HybridWakeWordEngine
except Exception:  # pragma: no cover — websockets not installed yet
    HybridWakeWordEngine = None  # type: ignore[assignment]

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


def _thread_pool_workers() -> int:
    raw = os.environ.get("KODA_THREAD_WORKERS", "").strip()
    if raw:
        try:
            return max(2, int(raw))
        except ValueError:
            warn(f"Invalid KODA_THREAD_WORKERS={raw!r}; using automatic value")
    cpu_count = os.cpu_count() or 4
    return max(4, min(12, cpu_count + 4))


def _install_runtime_thread_pool() -> ThreadPoolExecutor:
    workers = _thread_pool_workers()
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="koda-io")
    asyncio.get_running_loop().set_default_executor(executor)
    logger.info("Runtime thread pool ready (workers=%d)", workers)
    return executor


def _label(check_name: str) -> str:
    return _COMPONENT_LABELS.get(check_name, check_name)


async def _report_hardware() -> None:
    statuses = await run_full_check()
    detected = sum(1 for s in statuses if s.get("ok"))
    state("HW", f"{detected}/{len(statuses)} components detected")
    for status in statuses:
        glyph = "✓" if status.get("ok") else "✗"
        logger.info(
            "    %s  %-22s — %s",
            glyph,
            _label(status.get("name", "?")),
            status.get("message", ""),
        )


def _safe_open(label: str, opener: Callable[[], None]) -> bool:
    try:
        opener()
        logger.info("    ✓  %s opened", label)
        return True
    except Exception as exc:
        warn(f"{label} unavailable: {exc}")
        return False


async def _safe_async(label: str, awaitable: Awaitable) -> None:
    try:
        await awaitable
    except Exception:
        logger.exception("%s failed", label)


async def _safe_to_thread(label: str, func: Callable[[], object]) -> Optional[object]:
    try:
        return await asyncio.to_thread(func)
    except Exception:
        logger.exception("%s failed", label)
        return None


async def _open_adapters_parallel(
    nextion: NextionAdapter,
    arduino: ArduinoAdapter,
) -> None:
    state("BOOT", "opening serial adapters in parallel")
    await asyncio.gather(
        asyncio.to_thread(_safe_open, "Nextion", nextion.open),
        asyncio.to_thread(_safe_open, "Arduino", arduino.open),
    )


async def _prepare_doa_reader(
    doa_reader: DOAReader,
    config: AppConfig,
    rotation_calib: RotationCalibration,
) -> None:
    if not config.rotation.enabled:
        logger.info("Rotation auto desactivee (ROTATION_ENABLED=0)")
        return

    started = await asyncio.to_thread(doa_reader.start)
    if started:
        logger.info(
            "Rotation auto vers le locuteur activée (slope=%.1f°/s, offset=%.1f°, "
            "front=%.1f°, invert=%s, LUT=%d points)",
            rotation_calib.slope_deg_per_s, rotation_calib.offset_deg,
            rotation_calib.front_offset_deg, rotation_calib.invert_direction,
            len(rotation_calib.lut),
        )
    else:
        logger.warning("DOA reader unavailable — rotation toward speaker disabled")


async def _bootstrap_in_parallel(
    *,
    audio_output: AudioOutputAdapter,
    backend: BackendClient,
    wake_word: Optional[WakeWordService],
    display: DisplayService,
    motion: MotionService,
    nextion: NextionAdapter,
    arduino: ArduinoAdapter,
    doa_reader: DOAReader,
    config: AppConfig,
    rotation_calib: RotationCalibration,
) -> None:
    """Run every independent startup step concurrently.

    Dependency chain:
      1. `backend.start()` — instant (just builds an httpx.AsyncClient), but the
         health probe needs it, so we await it before the gather.
      2. Everything else has **no inter-dependency** and runs in one big
         `asyncio.gather`:
           - Bluetooth speaker connect      (5-15s — was the worst sequential cost)
           - Backend /health probe          (0.1-0.5s)
           - Vosk wake-word model load      (~8s on Pi 4)
           - DOA reader USB init            (~0.1s)
           - Display idle frame             (~0.05s UART)
           - Motion hello servo greeting    (~0.05s + servo travel)
      3. The slowest task (usually BT or Vosk) sets the total boot duration —
         the others piggy-back for free.
    """
    await backend.start()

    async def _bluetooth_task() -> None:
        if not config.audio_output.auto_connect:
            return
        connected = await audio_output.ensure_bluetooth()
        if connected:
            state("READY", f"Bluetooth speaker {config.audio_output.bluetooth_mac}")
        else:
            warn("Bluetooth speaker unavailable -- falling back to default sink")

    async def _health_task() -> None:
        ok = await backend.health()
        if not ok:
            warn("Backend health probe failed (continuing -- may recover)")

    tasks: list[asyncio.Task] = [
        asyncio.create_task(_bluetooth_task()),
        asyncio.create_task(_health_task()),
        asyncio.create_task(_prepare_doa_reader(doa_reader, config, rotation_calib)),
    ]
    if wake_word is not None:
        tasks.append(asyncio.create_task(
            _safe_async("wake_word.prepare", wake_word.prepare())
        ))
    if nextion.is_open:
        tasks.append(asyncio.create_task(
            _safe_async("display.resume_idle", display.resume_idle())
        ))
    if arduino.is_open:
        tasks.append(asyncio.create_task(
            _safe_async("motion.hello (greeting)", motion.hello())
        ))

    state("BOOT", f"bootstrapping {len(tasks)} services in parallel")
    await asyncio.gather(*tasks, return_exceptions=False)


async def _shutdown(
    backend: BackendClient,
    nextion: NextionAdapter,
    arduino: ArduinoAdapter,
    display: DisplayService,
    motion: MotionService,
) -> None:
    state("SHUTDOWN", "stopping all adapters")
    await _safe_async("display.set_expression(SLEEPING)", display.set_expression(Expression.SLEEPING))
    if arduino.is_open:
        await _safe_async("motion.stop", motion.stop())
    await _safe_async("backend.close", backend.close())
    await _safe_to_thread("nextion.close", nextion.close)
    await _safe_to_thread("arduino.close", arduino.close)
    state("SHUTDOWN", "stopped")


def _build_wake_word_service(
    config: AppConfig,
    audio: AudioService,
    backend: BackendClient,
    respeaker: RespeakerAdapter,
) -> Optional[WakeWordService]:
    if not config.wake_word.enabled:
        return None
    keywords = config.wake_word.keywords or DEFAULT_KEYWORDS
    matcher = WakeWordMatcher(list(keywords))

    engine = None
    if config.vosk.language and VoskWakeWordEngine is not None:
        try:
            if config.hybrid_wake_word.enabled and HybridWakeWordEngine is not None:
                engine = HybridWakeWordEngine(
                    respeaker=respeaker,
                    matcher=matcher,
                    language=config.vosk.language,
                    models_dir=config.vosk.models_dir,
                    chunk_bytes=config.vosk.chunk_bytes,
                    auto_download=config.vosk.auto_download,
                    backend_base_url=config.backend.base_url,
                    robot_id=config.robot_id,
                    awaiting_timeout_s=config.hybrid_wake_word.awaiting_timeout_s,
                    azure_language=config.hybrid_wake_word.azure_language or None,
                )
                logger.info(
                    "Wake-word engine: HYBRID (Vosk gate + Azure WS race, "
                    "vosk=%s, azure_lang=%s, awaiting=%.1fs)",
                    config.vosk.language,
                    config.hybrid_wake_word.azure_language or "<auto>",
                    config.hybrid_wake_word.awaiting_timeout_s,
                )
            else:
                engine = VoskWakeWordEngine(
                    respeaker=respeaker,
                    matcher=matcher,
                    language=config.vosk.language,
                    models_dir=config.vosk.models_dir,
                    chunk_bytes=config.vosk.chunk_bytes,
                    auto_download=config.vosk.auto_download,
                )
                logger.info("Wake-word engine: Vosk streaming (language=%s)", config.vosk.language)
        except Exception:
            logger.exception("Failed to construct wake-word engine; falling back to legacy chunk mode")
            engine = None
    else:
        logger.warning(
            "Vosk wake-word engine unavailable (language=%r, vosk_installed=%s) — "
            "using legacy chunked Azure-STT loop (less reliable)",
            config.vosk.language, VoskWakeWordEngine is not None,
        )

    return WakeWordService(
        audio=audio,
        backend=backend,
        matcher=matcher,
        chunk_seconds=config.wake_word.chunk_seconds,
        cooldown_seconds=config.wake_word.cooldown_seconds,
        engine=engine,
    )


async def _rotate_toward_speaker(
    doa_reader: Optional[DOAReader],
    motion: MotionService,
    config: AppConfig,
    *,
    label: str,
    wait_for_voice_s: float,
) -> None:
    """Read the DOA and pivot the chassis toward the current speaker.

    `wait_for_voice_s` controls how long we poll the XMOS voice-activity bit
    before falling back to the latest DOA snapshot:
      - **0.0** for the wake-word case — the angle is already fresh (the user
        just said the wake word, the XMOS register holds that direction).
      - **2–3s** for follow-up turns — the user may have moved silently
        between questions; we wait for the next utterance to read the angle
        at the right moment.
    """
    if doa_reader is None or not doa_reader.available or not config.rotation.enabled:
        return
    if not motion._adapter.is_open:
        return

    raw_angle = None
    source = "snapshot"
    if wait_for_voice_s > 0:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_for_voice_s
        while loop.time() < deadline:
            if await asyncio.to_thread(doa_reader.voice_active):
                raw_angle = await asyncio.to_thread(doa_reader.read_angle)
                if raw_angle is not None:
                    source = "voice"
                    break
            await asyncio.sleep(0.1)
    if raw_angle is None:
        raw_angle = await asyncio.to_thread(doa_reader.read_angle)
    if raw_angle is None:
        logger.debug("DOA read returned None — skipping rotation (%s)", label)
        return

    signed = shortest_signed_angle(raw_angle, motion._rotation)
    logger.info(
        "🧭 DOA toward %s [%s]: raw=%d°  →  relative=%+.1f° (front=%.1f°, invert=%s)",
        label, source, raw_angle, signed,
        motion._rotation.front_offset_deg, motion._rotation.invert_direction,
    )
    await _safe_async(f"motion.rotate_by_angle({label})", motion.rotate_by_angle(signed))


async def _run_wake_word_loop(
    wake_word: WakeWordService,
    conversation: ConversationService,
    display: DisplayService,
    motion: MotionService,
    speech: SpeechService,
    config: AppConfig,
    stop_event: asyncio.Event,
    doa_reader: Optional[DOAReader] = None,
    face_recognition: Optional[FaceRecognitionService] = None,
) -> None:
    greeting_text = config.conversation.greeting_text or "أهلا بيك"

    while not stop_event.is_set():
        state("PASSIVE", f"{len(wake_word._matcher.keywords)} keywords")
        match = await wake_word.wait_for_wake(stop_event)
        if match is None:
            return

        state("WAKE", match.keyword)

        # ── Parallel kickoff: visuel + rotation + greeting + face-reco (FAF) ──
        # Aucune dépendance entre eux. La rotation tourne pendant que le greeting
        # parle, et la face-reco se cache en background pour le 1er tour.
        async def _greet():
            if greeting_text:
                await speech.speak(greeting_text)

        rotate_task: Optional[asyncio.Task] = None
        if doa_reader is not None and doa_reader.available and config.rotation.enabled:
            rotate_task = asyncio.create_task(_rotate_toward_speaker(
                doa_reader, motion, config, label="wake", wait_for_voice_s=0.0,
            ))
        if face_recognition is not None:
            face_recognition.fire_and_forget_refresh()

        await asyncio.gather(
            display.set_expression(Expression.SURPRISED),
            _greet(),
            return_exceptions=True,
        )
        # Ensure rotation is finished before opening the mic (motor noise would
        # poison the VAD threshold).
        if rotate_task is not None:
            try:
                await rotate_task
            except Exception:
                logger.exception("rotation task failed")

        # ── Listen immediately — no second rotation pass ──
        state("ACTIVE")
        await _safe_async("display.set_expression(THINKING)",
                          display.set_expression(Expression.THINKING))
        await conversation.listen_and_answer()

        # ── Follow-up turns ──
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

            state("ACTIVE", f"follow-up {consecutive_silences + 1}/{max_silences}")
            # Re-orient toward the speaker — but only briefly poll voice so the
            # user doesn't wait if they speak right away.
            await _rotate_toward_speaker(
                doa_reader, motion, config, label="follow", wait_for_voice_s=1.5,
            )
            had_speech = await _run_active_turn(conversation)
            if had_speech:
                consecutive_silences = 0
            else:
                consecutive_silences += 1
                state("SILENCE", f"{consecutive_silences}/{max_silences}")

        state("SLEEP")
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
    state("ACTIVE", "always-listening mode — wake word disabled")
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
    _install_runtime_thread_pool()
    state("BOOT", f"robot_id={config.robot_id}", backend=config.backend.base_url)
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
        native_channels=config.respeaker.native_channels,
        processed_channel_index=config.respeaker.processed_channel_index,
    )
    logger.info(
        "Mic capture: device=%s native_channels=%d → mono ch%d, rate=%d, denoise=%s",
        config.respeaker.alsa_device,
        config.respeaker.native_channels,
        config.respeaker.processed_channel_index,
        config.respeaker.sample_rate,
        "ON (sox remix on XMOS-processed channel)"
        if config.respeaker.native_channels > 1
        else "OFF (single-channel firmware)",
    )
    audio_output = AudioOutputAdapter(
        bluetooth_mac=config.audio_output.bluetooth_mac,
        pulse_sink=config.audio_output.pulse_sink,
    )

    await _open_adapters_parallel(nextion, arduino)

    backend = BackendClient(config.backend.base_url, config.backend.timeout_seconds)

    display = DisplayService(nextion)
    rotation_calib = RotationCalibration(
        slope_deg_per_s=config.rotation.slope_deg_per_s,
        offset_deg=config.rotation.offset_deg,
        min_duration_s=config.rotation.min_duration_s,
        settle_s=config.rotation.settle_s,
        deadband_deg=config.rotation.deadband_deg,
        front_offset_deg=config.rotation.front_offset_deg,
        invert_direction=config.rotation.invert_direction,
        lut=list(config.rotation.lut),
    )
    motion = MotionService(arduino, rotation_calib)
    audio = AudioService(respeaker, config.respeaker.record_seconds)

    doa_reader = DOAReader()
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

    music_cache_dir = Path("cache/music").resolve()
    music_player = MusicPlayer(audio_output, music_cache_dir, config.backend.base_url)
    logger.info("MusicPlayer ready (cache dir=%s, backend=%s)", music_cache_dir, config.backend.base_url)

    face_recognition: Optional[FaceRecognitionService] = None
    if config.face_recognition.enabled:
        camera = CameraAdapter(
            width=config.camera.width,
            height=config.camera.height,
            capture_timeout_ms=config.camera.capture_timeout_ms,
        )
        face_recognition = FaceRecognitionService(
            camera=camera,
            backend=backend,
            cache_seconds=config.face_recognition.cache_seconds,
            fallback_name=config.face_recognition.fallback_name,
        )
        logger.info(
            "Face recognition enabled (cache=%.0fs, camera=%dx%d, fallback=%r)",
            config.face_recognition.cache_seconds,
            config.camera.width, config.camera.height,
            config.face_recognition.fallback_name,
        )
    else:
        logger.info("Face recognition disabled (FACE_RECOGNITION_ENABLED=0)")

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
        music_player=music_player,
        face_recognition=face_recognition,
    )

    wake_word = _build_wake_word_service(config, audio, backend, respeaker)
    # Single bootstrap gather: backend.start + bluetooth + health probe + Vosk
    # model load + DOA init + display + motion all run concurrently. The
    # slowest task (bluetooth ~5-15s OR Vosk ~8s on Pi 4) sets the total cost.
    await _bootstrap_in_parallel(
        audio_output=audio_output,
        backend=backend,
        wake_word=wake_word,
        display=display,
        motion=motion,
        nextion=nextion,
        arduino=arduino,
        doa_reader=doa_reader,
        config=config,
        rotation_calib=rotation_calib,
    )

    state("READY", "KODA online")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        if wake_word is not None:
            await _run_wake_word_loop(wake_word, conversation, display, motion, speech, config, stop_event, doa_reader, face_recognition)
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
