from __future__ import annotations

import asyncio
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
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
from services.touch.touch_sensor_service import TouchSensorService
from services.vision.face_recognition_service import FaceRecognitionService
from services.wake_word.default_keywords import DEFAULT_KEYWORDS
from utils.subprocess_registry import kill_tracked_subprocesses, pkill_orphans
from services.wake_word.wake_word_matcher import WakeWordMatcher
from services.wake_word.wake_word_service import WakeWordService
from utils.logger import get_logger
from utils.states import error, state, warn

try:
    from services.wake_word.vosk_engine import VoskWakeWordEngine
except Exception:  # pragma: no cover — vosk not installed during early dev
    VoskWakeWordEngine = None  # type: ignore[assignment]

try:
    from services.wake_word.backend_ws_engine import BackendWsWakeWordEngine
except Exception:  # pragma: no cover
    BackendWsWakeWordEngine = None  # type: ignore[assignment]

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
        # Purely informational: produces a "degraded mode" log if the backend is
        # unreachable. It must NOT gate boot.
        try:
            await backend.health_with_retry(attempts=3, base_delay_s=1.0)
        except Exception:
            logger.debug("backend health probe task errored", exc_info=True)

    # Fire the health probe in the BACKGROUND and DON'T await it in the gather:
    # when the backend is slow/unreachable it takes ~48s (3 × 15s timeouts +
    # backoff), which froze the robot at startup before it could listen. The
    # robot runs in degraded mode anyway, so the probe never needs to block boot.
    asyncio.create_task(_health_task(), name="backend-health-probe")

    tasks: list[asyncio.Task] = [
        asyncio.create_task(_bluetooth_task()),
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
    # return_exceptions=True: one failed startup task (Bluetooth, health
    # probe, display, etc.) MUST NOT abort the others — we want to boot in
    # degraded mode rather than refuse to start at all. Each task's outcome
    # is logged individually below.
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for task, result in zip(tasks, results):
        if isinstance(result, BaseException):
            logger.warning(
                "Bootstrap task %r failed: %s — continuing in degraded mode",
                task.get_name(), result,
            )


async def _shutdown(
    backend: BackendClient,
    nextion: NextionAdapter,
    arduino: ArduinoAdapter,
    display: DisplayService,
    motion: MotionService,
    face_recognition: Optional[FaceRecognitionService] = None,
) -> None:
    state("SHUTDOWN", "stopping all adapters")
    if face_recognition is not None:
        await _safe_async("face_recognition.cancel_pending_refresh",
                          face_recognition.cancel_pending_refresh())
    await _safe_async("display.set_expression(SLEEPING)", display.set_expression(Expression.SLEEPING))
    if arduino.is_open:
        await _safe_async("motion.stop", motion.stop())
    # Cleanup any subprocess we leaked (sox/arecord/rpicam/yt-dlp) so they
    # don't keep holding the USB endpoint or the camera busy after exit.
    await _safe_async("kill_tracked_subprocesses", kill_tracked_subprocesses())
    # Belt-and-suspenders: also pkill orphans by name in case some were
    # spawned by code paths that don't use track_subprocess().
    killed = pkill_orphans()
    if killed:
        logger.info("Shutdown: pkill matched %d orphan binaries", killed)
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
    if config.hybrid_wake_word.enabled and config.hybrid_wake_word.mode in {
        "direct", "ws", "backend_ws", "azure",
    }:
        if BackendWsWakeWordEngine is not None:
            engine = BackendWsWakeWordEngine(
                respeaker=respeaker,
                matcher=matcher,
                language=config.hybrid_wake_word.azure_language or config.vosk.language or "ar-SA",
                keywords=keywords,
                backend_base_url=config.backend.base_url,
                robot_id=config.robot_id,
                chunk_bytes=config.vosk.chunk_bytes,
                log_partials=config.hybrid_wake_word.log_partials,
            )
            logger.info(
                "Wake-word engine: BACKEND-WS direct (Azure streaming, lang=%s, keywords=%d, chunk=%d)",
                config.hybrid_wake_word.azure_language or config.vosk.language or "ar-SA",
                len(keywords),
                config.vosk.chunk_bytes,
            )
        else:
            logger.warning("Backend WS wake-word engine unavailable; falling back")
        return WakeWordService(
            audio=audio,
            backend=backend,
            matcher=matcher,
            chunk_seconds=config.wake_word.chunk_seconds,
            cooldown_seconds=config.wake_word.cooldown_seconds,
            engine=engine,
        )

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
    sample_window_s: float,
) -> None:
    """Read the DOA and pivot the chassis toward the current speaker.

    We sample the XMOS DOA over ``sample_window_s`` WHILE the person speaks,
    keep only voice-active samples and return their circular mean (outliers
    rejected). A single instantaneous read is far too noisy: it gets pulled
    off by Pi-fan noise, reflections, or a stale value captured after the wake
    word already ended. See ``DOAReader.read_angle_stable``.

    ``sample_window_s`` is the listening window before we commit to an angle:
      - ~2s at the wake word — wait for the start of the user's utterance.
      - ~2s on follow-up turns — re-orient on the next thing they say.
    If not enough voiced samples are collected, we fall back to one snapshot
    rather than spin toward noise.
    """
    if doa_reader is None or not doa_reader.available or not config.rotation.enabled:
        return
    if not motion._adapter.is_open:
        return

    raw_angle = await asyncio.to_thread(doa_reader.read_angle_stable, sample_window_s)
    source = "stable"
    if raw_angle is None:
        # Too few voiced samples — last resort single read (better than nothing).
        raw_angle = await asyncio.to_thread(doa_reader.read_angle)
        source = "snapshot-fallback"
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
        try:
            await _wake_word_iteration(
                wake_word, conversation, display, motion, speech, config,
                stop_event, doa_reader, face_recognition, greeting_text,
            )
        except asyncio.CancelledError:
            # Normal shutdown path (signal handler) — propagate.
            raise
        except Exception:
            # Anything else (Vosk crash, Azure WS reset, USB disconnect mid-turn,
            # subprocess died, etc.) MUST NOT kill the loop. Log and back off
            # so we don't hot-spin if the failure is permanent.
            logger.exception("Wake-word loop iteration crashed — restarting in 2 s")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2.0)
                return  # shutdown requested during the back-off
            except asyncio.TimeoutError:
                continue


# Sentinelle renvoyée par _wait_for_wake_or_passive_greet quand c'est le
# bonjour autonome (passive_greet) qui s'est déclenché — et non un mot d'éveil.
# Le robot a déjà PARLÉ, donc l'appelant doit enchaîner directement sur l'écoute
# active (la réponse de l'utilisateur) SANS re-dire bonjour.
_PASSIVE_GREET_DONE = object()


async def _wait_for_wake_or_passive_greet(
    wake_word: WakeWordService,
    conversation: ConversationService,
    config: AppConfig,
    stop_event: asyncio.Event,
):
    """Attendre un mot de réveil, mais aussi déclencher ``passive_greet`` après
    ``passive_greet_interval_s`` secondes d'inactivité.

    Si la course est gagnée par le timer, on appelle ``conversation.passive_greet()``
    (face-id → /api/webhook/nom → TTS) puis on relance la course. Si c'est le
    wake-word qui gagne (ou ``stop_event``), on retourne sa valeur immédiatement.

    Reste rétro-compatible : si ``passive_greet_enabled=False``, on tombe sur
    l'appel direct à ``wait_for_wake`` (zéro overhead).
    """
    if not config.conversation.passive_greet_enabled:
        return await wake_word.wait_for_wake(stop_event)

    interval_s = max(30.0, float(config.conversation.passive_greet_interval_s))
    while not stop_event.is_set():
        wake_task = asyncio.create_task(
            wake_word.wait_for_wake(stop_event), name="wake-wait",
        )
        idle_task = asyncio.create_task(asyncio.sleep(interval_s), name="passive-greet-idle")
        done, pending = await asyncio.wait(
            {wake_task, idle_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        if wake_task in done:
            return wake_task.result()

        # Idle a gagné → démarrer une conversation autonome.
        state("PASSIVE-GREET", f"{interval_s:.0f}s idle — bonjour autonome")
        try:
            await conversation.passive_greet()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("passive_greet failed — back to passive listening")
            continue
        # KODA vient de parler tout seul → on rend la main pour qu'il ÉCOUTE
        # la réponse (mode actif), au lieu de retourner dormir directement.
        return _PASSIVE_GREET_DONE


async def _wake_word_iteration(
    wake_word: WakeWordService,
    conversation: ConversationService,
    display: DisplayService,
    motion: MotionService,
    speech: SpeechService,
    config: AppConfig,
    stop_event: asyncio.Event,
    doa_reader: Optional[DOAReader],
    face_recognition: Optional[FaceRecognitionService],
    greeting_text: str,
) -> None:
    """One wake → greet → answer → follow-ups → back-to-sleep cycle.

    Extracted from the main loop so the outer try/except can restart cleanly
    after any failure (Vosk crash, Azure timeout, motor jam, etc.) without
    re-importing the world.
    """
    state("PASSIVE", f"{len(wake_word._matcher.keywords)} keywords")
    match = await _wait_for_wake_or_passive_greet(
        wake_word, conversation, config, stop_event,
    )
    if match is None:
        return

    # Bonjour autonome : KODA a déjà parlé → on écoute la réponse tout de suite
    # (mode actif), sans re-dire bonjour ni re-tourner la tête.
    if match is _PASSIVE_GREET_DONE:
        await _run_active_conversation(
            conversation, display, motion, config, stop_event, doa_reader,
        )
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
            doa_reader, motion, config, label="wake", sample_window_s=2.0,
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

    # ── Active conversation: listen + follow-ups until the user goes quiet ──
    await _run_active_conversation(
        conversation, display, motion, config, stop_event, doa_reader,
    )


async def _run_active_conversation(
    conversation: ConversationService,
    display: DisplayService,
    motion: MotionService,
    config: AppConfig,
    stop_event: asyncio.Event,
    doa_reader: Optional[DOAReader],
) -> None:
    """Listen for the user's question, then keep answering follow-ups until the
    user stays silent for ``active_idle_timeout_s`` — then return so the caller
    drops back to passive (sleep / wake-word) listening.

    Shared by the wake-word path and the post-touch resume path so a touch
    interrupt lands KODA straight back at "waiting for your question" instead of
    all the way back at the wake word.
    """
    state("ACTIVE")
    await _safe_async("display.set_expression(THINKING)",
                      display.set_expression(Expression.THINKING))
    await conversation.listen_and_answer()

    idle_timeout = max(2.0, config.conversation.active_idle_timeout_s)
    while not stop_event.is_set():
        # Brief pause so KODA doesn't re-open the mic on top of its own TTS tail.
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=config.conversation.inter_turn_pause_seconds,
            )
            return
        except asyncio.TimeoutError:
            pass

        state("ACTIVE", f"follow-up (idle {idle_timeout:.0f}s)")
        await _rotate_toward_speaker(
            doa_reader, motion, config, label="follow", sample_window_s=2.0,
        )
        had_speech = await _run_followup_turn(conversation, idle_timeout)
        if not had_speech:
            break  # silent for the whole idle window → back to sleep

    state("SLEEP")
    await _safe_async("display.resume_idle", display.resume_idle())


async def _run_followup_turn(conversation: ConversationService, idle_timeout_s: float) -> bool:
    """One follow-up turn bounded by ``idle_timeout_s``. True if speech heard."""
    try:
        return await conversation.listen_for_followup(idle_timeout_s)
    except Exception:
        logger.exception("Follow-up turn failed")
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


def _create_behavior_task(
    wake_word: Optional[WakeWordService],
    conversation: ConversationService,
    display: DisplayService,
    motion: MotionService,
    speech: SpeechService,
    config: AppConfig,
    stop_event: asyncio.Event,
    doa_reader: Optional[DOAReader],
    face_recognition: Optional[FaceRecognitionService],
) -> asyncio.Task:
    if wake_word is not None:
        return asyncio.create_task(
            _run_wake_word_loop(
                wake_word,
                conversation,
                display,
                motion,
                speech,
                config,
                stop_event,
                doa_reader,
                face_recognition,
            ),
            name="koda-wake-word-loop",
        )
    return asyncio.create_task(
        _run_legacy_loop(conversation, config, stop_event),
        name="koda-legacy-loop",
    )


async def _cancel_behavior_task(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Robot behavior task failed while being interrupted")


async def _play_tickle_laugh(
    display: DisplayService,
    speech: SpeechService,
    audio_output: AudioOutputAdapter,
    config: AppConfig,
) -> None:
    await _safe_async("display.set_expression(HAPPY)", display.set_expression(Expression.HAPPY))
    try:
        wav_path = config.touch.laugh_wav_path
        if wav_path:
            path = Path(wav_path).expanduser()
            if path.exists():
                await audio_output.play_wav_file(path)
                return
            logger.warning("TOUCH_LAUGH_WAV does not exist: %s", path)
        await speech.speak(config.touch.laugh_text)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Tickle laugh playback failed")
    finally:
        await _safe_async("display.resume_idle", display.resume_idle())


async def _handle_touch_interrupt(
    behavior_task: asyncio.Task,
    display: DisplayService,
    motion: MotionService,
    speech: SpeechService,
    audio_output: AudioOutputAdapter,
    config: AppConfig,
) -> None:
    state("TOUCH", "tickle interrupt")
    motion.request_abort()
    behavior_task.cancel()

    await asyncio.gather(
        _safe_async("audio_output.stop_playback", audio_output.stop_playback()),
        _safe_async("motion.stop", motion.stop()),
        return_exceptions=True,
    )
    await _cancel_behavior_task(behavior_task)
    # Let ALSA release the mic the cancelled task just freed — its listener /
    # wake-word engine `finally` already terminates THEIR own sox. Do NOT call
    # the global kill_tracked_subprocesses() here: it is a shutdown-level nuke
    # that also kills the wake-word mic the resumed loop is about to reopen,
    # which left KODA hung after a touch. A short settle avoids device contention.
    await asyncio.sleep(0.3)
    await _play_tickle_laugh(display, speech, audio_output, config)


def _create_post_touch_task(
    wake_word: Optional[WakeWordService],
    conversation: ConversationService,
    display: DisplayService,
    motion: MotionService,
    speech: SpeechService,
    config: AppConfig,
    stop_event: asyncio.Event,
    doa_reader: Optional[DOAReader],
    face_recognition: Optional[FaceRecognitionService],
) -> asyncio.Task:
    """After a touch interrupt: drop straight into listening for the user's
    next question (active, no wake word, no greeting), then resume the normal
    passive wake-word loop once the conversation goes quiet."""
    async def _runner() -> None:
        await _run_active_conversation(
            conversation, display, motion, config, stop_event, doa_reader,
        )
        if stop_event.is_set():
            return
        if wake_word is not None:
            await _run_wake_word_loop(
                wake_word, conversation, display, motion, speech, config,
                stop_event, doa_reader, face_recognition,
            )
        else:
            await _run_legacy_loop(conversation, config, stop_event)

    return asyncio.create_task(_runner(), name="koda-post-touch")


async def _run_robot_loop_with_touch(
    *,
    wake_word: Optional[WakeWordService],
    conversation: ConversationService,
    display: DisplayService,
    motion: MotionService,
    speech: SpeechService,
    audio_output: AudioOutputAdapter,
    config: AppConfig,
    stop_event: asyncio.Event,
    doa_reader: Optional[DOAReader],
    face_recognition: Optional[FaceRecognitionService],
    touch: TouchSensorService,
) -> None:
    touch_event = asyncio.Event()

    def on_touch() -> None:
        touch_event.set()

    if not touch.start(on_touch):
        if wake_word is not None:
            await _run_wake_word_loop(
                wake_word,
                conversation,
                display,
                motion,
                speech,
                config,
                stop_event,
                doa_reader,
                face_recognition,
            )
        else:
            await _run_legacy_loop(conversation, config, stop_event)
        return

    behavior_task = _create_behavior_task(
        wake_word,
        conversation,
        display,
        motion,
        speech,
        config,
        stop_event,
        doa_reader,
        face_recognition,
    )
    stop_task = asyncio.create_task(stop_event.wait(), name="koda-stop-wait")

    try:
        while not stop_event.is_set():
            touch_task = asyncio.create_task(touch_event.wait(), name="koda-touch-wait")
            done, _ = await asyncio.wait(
                {behavior_task, stop_task, touch_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if touch_task not in done:
                touch_task.cancel()
                with suppress(asyncio.CancelledError):
                    await touch_task

            if stop_task in done:
                break

            if behavior_task in done:
                await behavior_task
                break

            if touch_task in done:
                touch_event.clear()
                await _handle_touch_interrupt(
                    behavior_task,
                    display,
                    motion,
                    speech,
                    audio_output,
                    config,
                )
                if stop_event.is_set():
                    break
                # Resume by LISTENING FOR THE QUESTION (active), not back at the
                # wake word — touch means "stop, I want to ask/say something".
                behavior_task = _create_post_touch_task(
                    wake_word,
                    conversation,
                    display,
                    motion,
                    speech,
                    config,
                    stop_event,
                    doa_reader,
                    face_recognition,
                )
    finally:
        touch.close()
        stop_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task
        if not behavior_task.done():
            await _cancel_behavior_task(behavior_task)


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

    backend = BackendClient(
        config.backend.base_url,
        config.backend.timeout_seconds,
        face_api_base_url=config.face_recognition.api_base_url,
    )

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
            prefer_mjpeg=config.camera.prefer_mjpeg,
            mjpeg_url=config.camera.mjpeg_url,
            mjpeg_timeout_s=config.camera.mjpeg_timeout_s,
            stream_width=config.camera.stream_width,
            stream_height=config.camera.stream_height,
            stream_fps=config.camera.stream_fps,
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

    touch = TouchSensorService(
        enabled=config.touch.enabled,
        pins=config.touch.pins,
        active_high=config.touch.active_high,
        pull_up=config.touch.pull_up,
        bounce_seconds=config.touch.bounce_seconds,
        cooldown_seconds=config.touch.cooldown_seconds,
    )

    try:
        await _run_robot_loop_with_touch(
            wake_word=wake_word,
            conversation=conversation,
            display=display,
            motion=motion,
            speech=speech,
            audio_output=audio_output,
            config=config,
            stop_event=stop_event,
            doa_reader=doa_reader,
            face_recognition=face_recognition,
            touch=touch,
        )
    finally:
        await _shutdown(backend, nextion, arduino, display, motion, face_recognition)


def main() -> None:
    config = load_config()
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
