"""Default keyword variants for the wake-word "محسن" (Mohsen).

This list intentionally mirrors the working WebSocket smoke test
``test_ws.py respeaker`` so the runtime robot and the manual test send the
same wake-word configuration to the backend.
"""

from __future__ import annotations


DEFAULT_KEYWORDS: tuple[str, ...] = (
    "محسن",
    "مهسن",
    "موهسن",
    "موهسين",
    "mohsen",
    "mohsene",
    "mohcen",
    "mohssen",
    "mouhsen",
    "moohsen",
    "mosen",
    "mohsène",
    "mohsain",
    "koda",
    "كودا",
)
