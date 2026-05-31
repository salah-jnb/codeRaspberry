from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

# Compact format: HH:MM:SS  <module6>  message
# Set LOG_FORMAT=verbose in .env to get the old long format.
_VERBOSE_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_VERBOSE_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Optional rotating file log. Enabled when KODA_LOG_FILE is non-empty.
# Default location ~/.koda/logs/koda.log so it works without sudo. Set to
# /var/log/koda/koda.log if you want central system logs (chown pi the dir).
_DEFAULT_LOG_FILE = str(Path.home() / ".koda" / "logs" / "koda.log")
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024   # 10 MB
_DEFAULT_BACKUP_COUNT = 5               # → 50 MB max on disk

# Module-level guard so we only attach the file handler once even if multiple
# get_logger calls race.
_FILE_HANDLER_INSTALLED = False


def _resolve_level() -> int:
    raw = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO)


def _install_file_handler_once() -> None:
    """Attach a RotatingFileHandler to the root logger so EVERY logger inherits
    it. Idempotent. Disabled if KODA_LOG_FILE=0 / no / empty."""
    global _FILE_HANDLER_INSTALLED
    if _FILE_HANDLER_INSTALLED:
        return

    raw_path = os.environ.get("KODA_LOG_FILE", _DEFAULT_LOG_FILE).strip()
    if raw_path in {"", "0", "no", "false", "off"}:
        _FILE_HANDLER_INSTALLED = True  # don't keep trying
        return

    try:
        path = Path(raw_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = int(os.environ.get("KODA_LOG_MAX_BYTES", _DEFAULT_MAX_BYTES))
        backup_count = int(os.environ.get("KODA_LOG_BACKUP_COUNT", _DEFAULT_BACKUP_COUNT))
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(_VERBOSE_FORMAT, datefmt=_VERBOSE_DATEFMT))
        handler.setLevel(_resolve_level())
        root = logging.getLogger()
        root.addHandler(handler)
        # Root must accept the level too, otherwise records are filtered before
        # they reach the handler.
        if root.level == logging.NOTSET or root.level > handler.level:
            root.setLevel(handler.level)
        # Tell the user where the logs go — once.
        logging.getLogger(__name__).info(
            "KODA file log: %s (rotation %d MB × %d files)",
            path, max_bytes // (1024 * 1024), backup_count,
        )
    except Exception as exc:
        # Don't crash KODA just because we can't write to disk.
        logging.getLogger(__name__).warning(
            "Failed to set up file log at %r: %s — stdout-only logging",
            raw_path, exc,
        )
    finally:
        _FILE_HANDLER_INSTALLED = True


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
    """`HH:MM:SS.ms <module7> <msg>` — no level/date noise. Millisecond precision
    is essential for measuring latency between WAKE / GREET / ACTIVE / response
    arrival. Set LOG_FORMAT=verbose for the full format with date + level.
    """

    def format(self, record: logging.LogRecord) -> str:
        # logging.LogRecord.created is a float epoch with µs precision; we keep
        # 3 digits of fractional seconds (millis) to avoid noise.
        ms = int((record.created - int(record.created)) * 1000)
        ts = self.formatTime(record, "%H:%M:%S")
        mod = _short_module(record.name)
        prefix = ""
        if record.levelno >= logging.ERROR:
            prefix = "❌ "
        elif record.levelno >= logging.WARNING:
            prefix = "⚠️  "
        return f"{ts}.{ms:03d} {mod:<7s} {prefix}{record.getMessage()}"


def get_logger(name: str) -> logging.Logger:
    # Ensure the rotating file handler is installed before any record flows
    # (the root logger captures them via propagate=True for the file handler).
    _install_file_handler_once()

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        if os.environ.get("LOG_FORMAT", "").strip().lower() == "verbose":
            handler.setFormatter(logging.Formatter(_VERBOSE_FORMAT, datefmt=_VERBOSE_DATEFMT))
        else:
            handler.setFormatter(_CompactFormatter())
        logger.addHandler(handler)
        # Keep stdout exclusive on the per-logger handler — but DO propagate
        # so the root's RotatingFileHandler also receives the records.
        logger.propagate = True
    logger.setLevel(_resolve_level())
    return logger
