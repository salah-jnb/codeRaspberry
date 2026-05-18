"""Record a few seconds from the ReSpeaker and save the WAV to /tmp.

Run:
    python -m scripts.test_respeaker             # 3s recording
    python -m scripts.test_respeaker --seconds 5
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.respeaker_adapter import RespeakerAdapter
from app.config import load_config
from utils.logger import get_logger

logger = get_logger("test_respeaker")


async def main(seconds: float) -> int:
    config = load_config()
    adapter = RespeakerAdapter(
        alsa_device=config.respeaker.alsa_device,
        sample_rate=config.respeaker.sample_rate,
        channels=config.respeaker.channels,
        sample_format=config.respeaker.sample_format,
    )

    logger.info("Recording %.1fs from %s ...", seconds, config.respeaker.alsa_device)
    try:
        wav = await adapter.record(seconds)
    except Exception as exc:
        logger.exception("Recording failed: %s", exc)
        return 1

    out_path = Path("/tmp/koda_respeaker_test.wav")
    out_path.write_bytes(wav)
    logger.info("Wrote %d bytes to %s", len(wav), out_path)
    logger.info("Inspect: file %s ; play: paplay %s", out_path, out_path)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.seconds)))
