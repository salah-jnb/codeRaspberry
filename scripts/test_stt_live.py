"""Real-time-ish STT test.

Records short audio chunks from the ReSpeaker and posts each to the backend's
`/api/audio/speech-to-text` endpoint. Prints the transcription as it comes
back, so you can read a paragraph and check what Azure heard segment by segment.

No new dependencies — uses the existing `BackendClient` and `RespeakerAdapter`.

Run:
    python -m scripts.test_stt_live                       # 4s chunks, 2 min max
    python -m scripts.test_stt_live --chunk 3 --max 60
    python -m scripts.test_stt_live --pipeline            # also POST to /speech-to-n8n-to-speech
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.audio_output_adapter import AudioOutputAdapter
from adapters.backend_client import BackendClient
from adapters.respeaker_adapter import RespeakerAdapter
from app.config import load_config
from utils.logger import get_logger

logger = get_logger("test_stt_live")


# ANSI colors — most Pi terminals handle these + Arabic RTL natively.
GRN = "\033[1;32m"
DIM = "\033[2;37m"
CYN = "\033[1;36m"
YEL = "\033[1;33m"
RED = "\033[1;31m"
RST = "\033[0m"


def _print_header(cfg) -> None:
    print(f"{CYN}=== Real-time STT test (HTTP chunks){RST}")
    print(f"  Backend       : {cfg.backend.base_url}")
    print(f"  Mic device    : {cfg.respeaker.alsa_device}")
    print(f"  Sample rate   : {cfg.respeaker.sample_rate} Hz mono")
    print(f"  Voice (TTS)   : {cfg.backend.voice_name or '(auto via setting.langue)'}")
    print()


async def _run_one_chunk(
    backend: BackendClient,
    respeaker: RespeakerAdapter,
    chunk_seconds: float,
    do_pipeline: bool,
    speech_out: AudioOutputAdapter | None,
) -> tuple[str, float]:
    """Record `chunk_seconds`, send to STT, optionally run full pipeline + play reply.

    Returns (transcript, elapsed_seconds).
    """
    t0 = time.monotonic()
    print(f"{YEL}▶ Recording {chunk_seconds:.1f}s ...{RST}", end="", flush=True)
    wav = await respeaker.record(chunk_seconds)
    rec_t = time.monotonic() - t0
    print(f"\r{YEL}▶ Recorded  {chunk_seconds:.1f}s  ({len(wav)} bytes in {rec_t:.1f}s){RST}")

    t1 = time.monotonic()
    try:
        transcript = await backend.speech_to_text(wav)
    except Exception as exc:
        detail = getattr(getattr(exc, "response", None), "text", "")
        print(f"{RED}✗ STT error: {(detail or str(exc))[:300]}{RST}")
        return "", time.monotonic() - t0
    stt_t = time.monotonic() - t1
    transcript = (transcript or "").strip()

    if transcript:
        print(f"{GRN}✅ STT [{stt_t:.2f}s]:{RST} {transcript}")
    else:
        print(f"{DIM}… STT [{stt_t:.2f}s]: (empty — silence or unrecognized){RST}")

    if do_pipeline and transcript:
        t2 = time.monotonic()
        try:
            reply_wav = await backend.speech_to_n8n_to_speech(wav, extra_text="salah")
        except Exception as exc:
            detail = getattr(getattr(exc, "response", None), "text", "")
            print(f"{RED}✗ Pipeline error: {(detail or str(exc))[:300]}{RST}")
            return transcript, time.monotonic() - t0
        pipe_t = time.monotonic() - t2
        print(f"{CYN}💬 Pipeline [{pipe_t:.2f}s]: reply WAV {len(reply_wav)} bytes{RST}")
        if speech_out is not None:
            tmp = Path(tempfile.mkstemp(suffix=".wav", prefix="stt_live_")[1])
            try:
                tmp.write_bytes(reply_wav)
                await speech_out.play_wav_file(tmp)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    return transcript, time.monotonic() - t0


async def main(chunk_seconds: float, max_seconds: float, do_pipeline: bool) -> int:
    cfg = load_config()
    _print_header(cfg)

    respeaker = RespeakerAdapter(
        alsa_device=cfg.respeaker.alsa_device,
        sample_rate=cfg.respeaker.sample_rate,
        channels=cfg.respeaker.channels,
        sample_format=cfg.respeaker.sample_format,
    )
    backend = BackendClient(cfg.backend.base_url, cfg.backend.timeout_seconds)
    speech_out: AudioOutputAdapter | None = None
    if do_pipeline:
        speech_out = AudioOutputAdapter(
            bluetooth_mac=cfg.audio_output.bluetooth_mac,
            pulse_sink=cfg.audio_output.pulse_sink,
        )

    await backend.start()
    transcripts: list[str] = []
    try:
        if not await backend.health():
            print(f"{RED}✗ Backend health check failed at {cfg.backend.base_url}{RST}")
            return 1

        print(f"{YEL}Speak now — Ctrl+C to stop. Each ▶ block records {chunk_seconds:.1f}s.{RST}\n")
        elapsed = 0.0
        chunk_id = 0
        while elapsed < max_seconds:
            chunk_id += 1
            print(f"{DIM}── chunk #{chunk_id} ──{RST}")
            transcript, dt = await _run_one_chunk(
                backend, respeaker, chunk_seconds, do_pipeline, speech_out,
            )
            if transcript:
                transcripts.append(transcript)
            elapsed += dt
    except KeyboardInterrupt:
        print(f"\n{YEL}↩ Stopped by user{RST}")
    finally:
        await backend.close()

    if transcripts:
        print(f"\n{CYN}── Final transcript ({len(transcripts)} segments) ──{RST}")
        for i, t in enumerate(transcripts, 1):
            print(f"  {i}. {t}")
        print(f"\n{CYN}── Concatenated ──{RST}")
        print(" ".join(transcripts))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk", type=float, default=4.0,
                        help="Durée d'un chunk en secondes (défaut 4)")
    parser.add_argument("--max", type=float, default=120.0,
                        help="Durée max totale en secondes (défaut 120)")
    parser.add_argument("--pipeline", action="store_true",
                        help="Active aussi le pipeline complet speech-to-n8n-to-speech + playback BT")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.chunk, args.max, args.pipeline)))
