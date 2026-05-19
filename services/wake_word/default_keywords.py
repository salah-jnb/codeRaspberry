"""Default keyword variants for the wake-word "محسن" (Mohsen).

Curated list mirrors common STT mistakes (similar Arabic letters, Latin
transliterations, with and without short vowels). Add new entries here
when production logs reveal new mistakes — duplicates are silently dropped.
"""
from __future__ import annotations

DEFAULT_KEYWORDS: tuple[str, ...] = (
    # Arabic — main variants
    "محسن", "محسين", "مهسن", "مهسين", "محسان", "مهسان", "محصن", "مهصن",
    # Arabic — frequent STT confusions (ن → م / ب / ف / د / ر / ل)
    "محسم", "مهسم", "محسب", "محسف", "محسد", "محسر", "محسل",
    # Arabic — ح variations (ج / م / خ / ع)
    "مجسن", "ممسن", "مخسن", "معسن",
    # Arabic — س variations (ش / ز / ص / ض)
    "محشن", "محزن", "محصن", "محضن",
    # Arabic — with ة / ه
    "محسنه", "محسنة", "مهسنه",
    # Arabic — spaced (STT splitting tokens)
    "مح سن", "م حسن",
    # Arabic — initial م dropped by STT (frequent: Vosk hears "حسين"/"حسن"/"حسان")
    "حسين", "حسن", "حسان", "حسنين",
    "هسين", "هسن",
    # Latin — main transliterations
    "mohsen", "mohcen", "mohseen", "mohceen", "mohsin",
    "muhsen", "muhsin", "muhcen",
    # Latin — frequent STT mistakes
    "mohsean", "mohsane", "mohsene", "mohsine", "mohsun",
    "mouhsen", "mouhcen", "mouhsin",
    "moxen", "mosen", "moseen",
    "mohssen", "mohccen", "mohcin", "mohksen", "mohxen",
)
