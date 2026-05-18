"""Probe the FastAPI distributeur and exercise TTS -> playback.

Run:
    python -m scripts.test_backend
    python -m scripts.test_backend --text "Bonjour Koda"
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.audio_output_adapter import AudioOutputAdapter
from adapters.backend_client import BackendClient
from app.config import load_config
from utils.logger import get_logger

logger = get_logger("test_backend")


async def main(text: str) -> int:
    config = load_config()
    backend = BackendClient(config.backend.base_url, config.backend.timeout_seconds)
    audio = AudioOutputAdapter(
        bluetooth_mac=config.audio_output.bluetooth_mac,
        pulse_sink=config.audio_output.pulse_sink,
    )

    await backend.start()
    try:
        logger.info("Probing %s ...", config.backend.base_url)
        if not await backend.health():
            logger.error("Backend health check failed")
            return 1

        logger.info("Synthesizing %r (voice=%s)", text, config.backend.voice_name)
        wav = await backend.text_to_speech(text, config.backend.voice_name)
        logger.info("Got %d bytes of WAV", len(wav))

        out_path = Path("/tmp/koda_backend_tts.wav")
        out_path.write_bytes(wav)
        logger.info("Saved to %s", out_path)

        logger.info("Playing through default audio output ...")
        await audio.play_wav_file(out_path)
        logger.info("Playback finished")
    finally:
        await backend.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="مرحبا، أنا كودا")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.text)))
