from __future__ import annotations

import logging
import os

# Compact format: HH:MM:SS  <module6>  message
# Set LOG_FORMAT=verbose in .env to get the old long format.
_VERBOSE_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_VERBOSE_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _resolve_level() -> int:
    raw = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO)


def _short_module(name: str) -> str:
    """services.wake_word.vosk_engine -> vosk
       adapters.respeaker_adapter -> respeak
       __main__ / app.main -> koda"""
    tail = name.rsplit(".", 1)[-1]
    if tail in ("__main__", "main"):
        return "koda"
    return (
        tail.removesuffix("_engine")
            .removesuffix("_service")
            .removesuffix("_adapter")
            .removesuffix("_reader")
            .removesuffix("_dispatcher")
            .removesuffix("_player")
            .replace("_", "")[:7]
    )


class _CompactFormatter(logging.Formatter):
    """`HH:MM:SS <module7> <msg>` — no level/date noise. Level shows as emoji
    only when severity is high enough to warrant it."""

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        mod = _short_module(record.name)
        prefix = ""
        if record.levelno >= logging.ERROR:
            prefix = "❌ "
        elif record.levelno >= logging.WARNING:
            prefix = "⚠️  "
        return f"{ts} {mod:<7s} {prefix}{record.getMessage()}"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        if os.environ.get("LOG_FORMAT", "").strip().lower() == "verbose":
            handler.setFormatter(logging.Formatter(_VERBOSE_FORMAT, datefmt=_VERBOSE_DATEFMT))
        else:
            handler.setFormatter(_CompactFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(_resolve_level())
    return logger
