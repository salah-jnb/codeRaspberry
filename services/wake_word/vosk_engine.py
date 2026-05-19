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

import asyncio
import json
from pathlib import Path
from typing import Optional

from adapters.respeaker_adapter import RespeakerAdapter
from services.wake_word.wake_word_matcher import WakeMatch, WakeWordMatcher
from utils.logger import get_logger

logger = get_logger(__name__)


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
        nonzero_chunks = 0
        last_logged_partial = ""
        try:
            async for chunk in stream:
                if stop_event is not None and stop_event.is_set():
                    return None

                chunks_seen += 1
                # Cheap audio-level probe: if every byte is 0/255 across many chunks,
                # the mic stream is dead (no signal). Useful diagnostic when nothing
                # is being recognised at all.
                if chunk and any(b not in (0, 0xFF) for b in chunk[:64]):
                    nonzero_chunks += 1
                if chunks_seen % 40 == 0:  # ~10s @ 250ms chunks
                    logger.info(
                        "Vosk heartbeat — %d chunks streamed, %d with audio signal "
                        "(last partial: %r)",
                        chunks_seen, nonzero_chunks, last_logged_partial[:80] or "<empty>",
                    )

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
            await stream.aclose()
        return None
