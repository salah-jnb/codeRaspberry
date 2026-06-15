"""Hybrid Vosk (sleep gate) + Azure WS (precision wake-word) recognizer.

State machine
=============
    ┌───────────────────────────────────────────────────────────────┐
    │ SLEEP                                                          │
    │   • Vosk consumes mic, scanning for ANY non-empty partial.    │
    │   • Azure WS is closed; zero cloud cost while silent.         │
    │   • CPU ≈ 15% on Pi 4.                                        │
    │                                                                │
    │   ── speech detected ──► AWAITING                              │
    └───────────────────────────────────────────────────────────────┘
    ┌───────────────────────────────────────────────────────────────┐
    │ AWAITING (≤ N seconds)                                         │
    │   • Vosk stays alive, matching against the keyword list.      │
    │   • Azure WS opens, also matching against the same list.      │
    │   • First match wins (race). On timeout → SLEEP.              │
    │                                                                │
    │   ── first match (Vosk OR Azure) ──► return WakeMatch          │
    │   ── timeout ──► SLEEP                                         │
    └───────────────────────────────────────────────────────────────┘

Why this layout?
    Vosk alone is fast + free but Tunisian accent + noisy rooms hurt its
    recall on "محسن". Azure has the precision but costs money. By using
    Vosk as the "is someone talking?" gate, we open Azure only when it
    matters and the running cost stays near zero in idle homes.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

from adapters.respeaker_adapter import RespeakerAdapter
from services.audio.pcm_broadcaster import PcmBroadcaster, queue_iterator
from services.wake_word.azure_streaming_client import AzureStreamingWakeWordClient
from services.wake_word.vosk_engine import VoskWakeWordEngine, WakeMatch
from services.wake_word.wake_word_matcher import WakeWordMatcher
from utils.logger import get_logger
# Alias: ce module utilise déjà une variable locale `state` (machine SLEEP/
# AWAITING), donc on importe le journal sous un autre nom.
from utils.states import state as journal

logger = get_logger(__name__)


class HybridWakeWordEngine:
    """Drop-in replacement for VoskWakeWordEngine that adds the Azure WS race.

    The constructor takes everything the Vosk engine wants plus the backend
    URL + robot id needed to open the Azure WebSocket.
    """

    def __init__(
        self,
        respeaker: RespeakerAdapter,
        matcher: WakeWordMatcher,
        *,
        language: str,
        models_dir: str,
        chunk_bytes: int = 8000,
        auto_download: bool = True,
        backend_base_url: str,
        robot_id: str,
        awaiting_timeout_s: float = 6.0,
        azure_language: Optional[str] = None,
    ) -> None:
        self._respeaker = respeaker
        self._matcher = matcher
        self._chunk_bytes = chunk_bytes
        self._backend_base_url = backend_base_url
        self._robot_id = robot_id
        self._awaiting_timeout_s = awaiting_timeout_s
        # Azure recognises BCP-47 locales (ar-SA), Vosk uses short codes (ar);
        # let callers override the Azure-side language if their robot is on
        # a different locale than the local Vosk model.
        self._azure_language = azure_language or self._vosk_language_to_azure(language)
        self._vosk = VoskWakeWordEngine(
            respeaker=respeaker,
            matcher=matcher,
            language=language,
            models_dir=models_dir,
            chunk_bytes=chunk_bytes,
            auto_download=auto_download,
        )

    @staticmethod
    def _vosk_language_to_azure(vosk_lang: str) -> str:
        # Vosk uses short codes; Azure needs BCP-47.
        return {"ar": "ar-SA", "en": "en-US", "fr": "fr-FR", "es": "es-ES"}.get(
            vosk_lang.lower(), vosk_lang
        )

    async def prepare(self) -> None:
        await self._vosk.prepare()

    async def wait_for_wake(self, stop_event: Optional[asyncio.Event] = None) -> Optional[WakeMatch]:
        if self._vosk._model is None:
            await self.prepare()

        from vosk import KaldiRecognizer  # local import

        recognizer = KaldiRecognizer(self._vosk._model, self._respeaker.sample_rate)
        recognizer.SetWords(False)

        logger.info(
            "Hybrid wake-word engine listening (Vosk gate + Azure WS race, "
            "keywords=%d variants, chunk=%d bytes, awaiting_timeout=%.1fs)",
            len(self._matcher.keywords), self._chunk_bytes, self._awaiting_timeout_s,
        )

        broadcaster = PcmBroadcaster(self._respeaker.stream_pcm(self._chunk_bytes))
        await broadcaster.start()
        vosk_queue = await broadcaster.add_consumer()

        state = "SLEEP"
        awaiting_deadline = 0.0
        azure_client: Optional[AzureStreamingWakeWordClient] = None
        azure_queue: Optional[asyncio.Queue] = None

        last_partial = ""
        verbose_partials = os.environ.get("HYBRID_LOG_PARTIALS", "0").strip() in {"1", "true", "yes"}

        async def _enter_awaiting(trigger_text: str) -> None:
            nonlocal state, awaiting_deadline, azure_client, azure_queue
            logger.info("🟡 SLEEP → AWAITING (Vosk caught speech: %r)", trigger_text[:80])
            journal("LISTEN")
            state = "AWAITING"
            awaiting_deadline = time.perf_counter() + self._awaiting_timeout_s
            azure_queue = await broadcaster.add_consumer()
            client = AzureStreamingWakeWordClient(
                backend_base_url=self._backend_base_url,
                robot_id=self._robot_id,
                language=self._azure_language,
                keywords=list(self._matcher.keywords),
            )
            try:
                await client.start(queue_iterator(azure_queue))
                azure_client = client
                logger.info("🔵 Azure stream opened (lang=%s)", self._azure_language)
            except Exception:
                logger.exception("Azure WS open failed — staying with Vosk only this window")
                if azure_queue is not None:
                    await broadcaster.remove_consumer(azure_queue)
                    azure_queue = None
                azure_client = None

        async def _exit_awaiting(reason: str) -> None:
            nonlocal state, azure_client, azure_queue
            logger.info("🟢 AWAITING → SLEEP (%s)", reason)
            journal("DOZE")
            state = "SLEEP"
            if azure_client is not None:
                await azure_client.aclose()
                azure_client = None
            if azure_queue is not None:
                await broadcaster.remove_consumer(azure_queue)
                azure_queue = None

        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    return None

                # 1. Pull the next mic chunk for Vosk.
                chunk = await vosk_queue.get()
                if chunk is None:
                    return None

                # 2. Feed Vosk (in a thread — KaldiRecognizer.AcceptWaveform is blocking).
                final = await asyncio.to_thread(recognizer.AcceptWaveform, chunk)
                if final:
                    text = json.loads(recognizer.Result()).get("text", "")
                else:
                    text = json.loads(recognizer.PartialResult()).get("partial", "")

                if verbose_partials and text and text != last_partial:
                    logger.info("Vosk partial: %r", text[:120])
                last_partial = text

                stripped = text.strip()

                # 3. State logic.
                if state == "SLEEP":
                    if stripped:
                        # Any speech wakes the gate.
                        await _enter_awaiting(stripped)
                    continue

                # AWAITING ----------------------------------------------------
                # Did Vosk catch the keyword?
                vosk_match = self._matcher.match(text) if text else None
                if vosk_match and vosk_match.matched:
                    logger.info("🎯 Vosk wake match: keyword=%r in %r", vosk_match.keyword, text[:80])
                    return WakeMatch(
                        matched=True,
                        keyword=vosk_match.keyword,
                        raw_text=text,
                        normalized_text=vosk_match.normalized_text,
                        remainder="",
                    )

                # Did Azure already fire? (its reader task is independent)
                if azure_client is not None and azure_client.matched is not None:
                    m = azure_client.matched
                    logger.info("🎯 Azure wake match wins (%dms, %s): %r", m.latency_ms, m.source, m.transcript[:80])
                    return WakeMatch(
                        matched=True,
                        keyword=m.keyword,
                        raw_text=m.transcript,
                        normalized_text=self._matcher.match(m.transcript).normalized_text,
                        remainder="",
                    )

                if time.perf_counter() > awaiting_deadline:
                    await _exit_awaiting("timeout — no wake word detected")
                    # Reset the recognizer so accumulated partials don't bleed
                    # into the next SLEEP window.
                    recognizer = KaldiRecognizer(self._vosk._model, self._respeaker.sample_rate)
                    recognizer.SetWords(False)
                    last_partial = ""
        finally:
            if azure_client is not None:
                await azure_client.aclose()
            await broadcaster.aclose()
