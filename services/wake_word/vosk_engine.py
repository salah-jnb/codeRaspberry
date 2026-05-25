"""Offline streaming wake-word engine backed by Vosk.

A long-running :func:`RespeakerAdapter.stream_pcm` is plumbed straight into a
``KaldiRecognizer``. Partial results are matched against the wake-word matcher
on every chunk, so detection latency is bounded by Vosk's partial-decode rate
(typically <300 ms on a Pi 4 with the small models).

Why a separate engine layer rather than wiring Vosk directly into
``WakeWordService``?  Vosk is a heavy native dependency and the import is
deferred to keep tests and CI fast on machines without the wheel installed.
"""
from __future__ import annotations

import array
import asyncio
import json
import math
from pathlib import Path
from typing import Optional

from adapters.respeaker_adapter import RespeakerAdapter
from services.wake_word.wake_word_matcher import WakeMatch, WakeWordMatcher
from utils.logger import get_logger

logger = get_logger(__name__)


def _rms_s16le(pcm: bytes) -> int:
    """Root-mean-square amplitude of a signed-16-bit little-endian PCM chunk."""
    if not pcm:
        return 0
    # `array('h', pcm)` interprets the bytes as native short ints. On Linux/Pi
    # native is little-endian which matches S16_LE.
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0
    total = 0
    for s in samples:
        total += s * s
    return int(math.sqrt(total / len(samples)))


def _resolve_model_dir(language: str, models_dir: str) -> Path:
    """Find ``<models_dir>/<language>`` either relative to the repo root or CWD."""
    candidate = Path(models_dir)
    if not candidate.is_absolute():
        repo_root = Path(__file__).resolve().parents[2]
        candidate = repo_root / candidate
    target = candidate / language
    return target


class VoskWakeWordEngine:
    """Continuously stream microphone PCM into Vosk and surface wake-word hits."""

    def __init__(
        self,
        respeaker: RespeakerAdapter,
        matcher: WakeWordMatcher,
        *,
        language: str,
        models_dir: str,
        chunk_bytes: int = 8000,
        auto_download: bool = True,
    ) -> None:
        self._respeaker = respeaker
        self._matcher = matcher
        self._language = language
        self._models_dir = models_dir
        self._chunk_bytes = chunk_bytes
        self._auto_download = auto_download

        self._model = None
        self._model_path: Optional[Path] = None

    @property
    def model_path(self) -> Optional[Path]:
        return self._model_path

    async def prepare(self) -> None:
        """Ensure the Vosk model is present on disk and loaded into memory."""
        if self._model is not None:
            return

        path = _resolve_model_dir(self._language, self._models_dir)
        if not path.is_dir():
            if not self._auto_download:
                raise RuntimeError(
                    f"Vosk model not found at {path} and VOSK_AUTO_DOWNLOAD=0. "
                    f"Run `python -m scripts.download_vosk_model {self._language}`."
                )
            logger.info("Vosk model for %s missing locally — downloading…", self._language)
            from scripts.download_vosk_model import ensure_model  # noqa: WPS433

            path = await asyncio.to_thread(
                ensure_model,
                self._language,
                Path(self._models_dir).resolve() if Path(self._models_dir).is_absolute()
                else Path(__file__).resolve().parents[2] / self._models_dir,
            )

        from vosk import Model, SetLogLevel  # noqa: WPS433

        SetLogLevel(-1)
        logger.info("Loading Vosk model from %s", path)
        self._model = await asyncio.to_thread(Model, str(path))
        self._model_path = path
        logger.info("Vosk model ready (language=%s)", self._language)

    async def wait_for_wake(self, stop_event: Optional[asyncio.Event] = None) -> Optional[WakeMatch]:
        """Stream microphone audio until the wake word is recognised.

        Returns the matched :class:`WakeMatch` (with ``remainder`` cleared — the
        caller MUST start a fresh VAD-bounded capture for the actual question),
        or ``None`` when ``stop_event`` is set before any hit.
        """
        if self._model is None:
            await self.prepare()
        assert self._model is not None  # for type-checkers

        from vosk import KaldiRecognizer  # noqa: WPS433

        recognizer = KaldiRecognizer(self._model, self._respeaker.sample_rate)
        recognizer.SetWords(False)

        logger.info("Vosk wake-word engine listening (keywords=%d variants, chunk=%d bytes)",
                    len(self._matcher.keywords), self._chunk_bytes)

        stream = self._respeaker.stream_pcm(self._chunk_bytes)
        chunks_seen = 0
        rms_sum = 0.0
        rms_peak = 0
        last_logged_partial = ""
        try:
            async for chunk in stream:
                if stop_event is not None and stop_event.is_set():
                    return None

                chunks_seen += 1
                # Compute real RMS amplitude on the S16_LE PCM payload.
                # Speech at normal volume typically sits in [800, 5000].
                # < ~150 = effective silence or broken capture; > ~10000 = clipping.
                rms = _rms_s16le(chunk)
                rms_sum += rms
                if rms > rms_peak:
                    rms_peak = rms
                if chunks_seen % 40 == 0:  # ~10s @ 250ms chunks
                    avg = rms_sum / 40
                    quality = (
                        "SILENT (mic broken or muted)" if rms_peak < 150 else
                        "very quiet — speak louder or check mic gain" if rms_peak < 600 else
                        "OK"
                    )
                    logger.info(
                        "Vosk heartbeat — %d chunks, RMS avg=%d peak=%d [%s] | device=%s | last partial=%r",
                        chunks_seen, int(avg), rms_peak, quality,
                        self._respeaker._device,
                        last_logged_partial[:80] or "<empty>",
                    )
                    if rms_peak < 150 and chunks_seen == 40:
                        logger.warning(
                            "Capture is silent on %r. If arecord -D plughw:3,0 works in a manual test, "
                            "set RESPEAKER_DEVICE=plughw:3,0 in .env (or wpctl set-default to ReSpeaker for pipewire).",
                            self._respeaker._device,
                        )
                    rms_sum = 0.0
                    rms_peak = 0

                final = await asyncio.to_thread(recognizer.AcceptWaveform, chunk)
                if final:
                    text = json.loads(recognizer.Result()).get("text", "")
                    if text:
                        logger.info("Vosk FINAL: %r", text[:200])
                else:
                    text = json.loads(recognizer.PartialResult()).get("partial", "")
                    if text and text != last_logged_partial:
                        logger.info("Vosk partial: %r", text[:200])
                        last_logged_partial = text

                if not text:
                    continue

                match = self._matcher.match(text)
                if match.matched:
                    logger.info("Wake word detected (keyword=%s) in: %r",
                                match.keyword, text[:120])
                    # Reset recognizer state so the next pass starts clean.
                    recognizer.Reset()
                    return WakeMatch(
                        matched=True,
                        keyword=match.keyword,
                        raw_text=match.raw_text,
                        normalized_text=match.normalized_text,
                        # remainder intentionally blank: a partial Vosk hypothesis
                        # is not a reliable carrier for the user's question.
                        remainder="",
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Vosk streaming loop failed")
            raise
        finally:
            # async for does NOT auto-close the generator on return/exception.
            # We must aclose() explicitly so the arecord subprocess is killed
            # and the RespeakerAdapter lock is released before
            # ContinuousListenerService.listen() tries to spawn its own
            # `sox -t alsa` reader.
            logger.info("Closing Vosk audio stream...")
            await stream.aclose()
            logger.info("Vosk audio stream closed (mic lock released)")
        return None
