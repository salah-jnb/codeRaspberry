"""Pretty state-transition logging for KODA's main loop.

Each call prints a single line with a glyph + short tag + message.
Override SYMBOLS in your environment if your terminal mangles emoji.

Usage:
    from utils.states import state, error, warn
    state("BOOT", f"robot_id={cfg.robot_id}")
    state("PASSIVE", "waiting for wake word")
    state("WAKE",    f"keyword={match.keyword}")
    state("GREET",   '"ahla bik"')
    state("ACTIVE",  "listening (VAD, max 15s)")
    state("THINK",   "STT + n8n + TTS")
    state("SPEAK",   "playing reply")
    state("SILENCE", "2/3 in active mode")
    state("SLEEP",   "back to wake-word watch")
    warn("BT speaker not ready, falling back to default sink")
    error("backend pipeline failed", reason="503 Service Unavailable")
"""
from __future__ import annotations

from typing import Any

from utils.logger import get_logger

logger = get_logger("koda")

SYMBOLS: dict[str, str] = {
    "BOOT":     "🤖",
    "HW":       "🔌",
    "READY":    "✅",
    "PASSIVE":  "👂",
    "WAKE":     "🎯",
    "GREET":    "👋",
    "ACTIVE":   "🎤",
    "THINK":    "💭",
    "HEARD":    "📝",
    "REPLY":    "💬",
    "SPEAK":    "🔊",
    "SILENCE":  "🤫",
    "SLEEP":    "😴",
    "WARN":     "⚠️ ",
    "ERROR":    "❌",
    "SHUTDOWN": "🛑",
}


def _format_extras(extras: dict[str, Any]) -> str:
    if not extras:
        return ""
    return "  " + "  ".join(f"{k}={v}" for k, v in extras.items())


def state(tag: str, message: str = "", **extras: Any) -> None:
    symbol = SYMBOLS.get(tag, "•")
    body = f" {message}" if message else ""
    body += _format_extras(extras)
    logger.info("%s %s%s", symbol, tag, body)


def warn(message: str, **extras: Any) -> None:
    logger.warning("%s%s", message, _format_extras(extras))


def error(message: str, **extras: Any) -> None:
    logger.error("%s%s", message, _format_extras(extras))
