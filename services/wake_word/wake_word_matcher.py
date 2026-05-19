from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_ARABIC_DIACRITICS = re.compile(r"[ً-ْٰـ]")
_PUNCT = re.compile(r"[.,!?؟،;:\-\"'()«»\[\]]+")
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lower-case Latin, strip diacritics (Arabic + NFKD), unify Arabic variants."""
    if not text:
        return ""
    text = text.lower().strip()
    text = _ARABIC_DIACRITICS.sub("", text)
    text = text.replace("ـ", "")
    text = (
        text.replace("أ", "ا")
            .replace("إ", "ا")
            .replace("آ", "ا")
            .replace("ة", "ه")
            .replace("ى", "ي")
    )
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = _WHITESPACE.sub(" ", text)
    return text.strip()


@dataclass(frozen=True)
class WakeMatch:
    matched: bool
    keyword: str = ""
    raw_text: str = ""
    normalized_text: str = ""
    remainder: str = ""


class WakeWordMatcher:
    """Detects a wake-word in transcribed text with Arabic/Latin tolerant matching."""

    def __init__(self, keywords: list[str]) -> None:
        if not keywords:
            raise ValueError("WakeWordMatcher requires at least one keyword")
        normalized = {normalize(k) for k in keywords if k and k.strip()}
        normalized.discard("")
        if not normalized:
            raise ValueError("No usable keywords after normalization")
        self._keywords: tuple[str, ...] = tuple(sorted(normalized, key=len, reverse=True))

    @property
    def keywords(self) -> tuple[str, ...]:
        return self._keywords

    def match(self, text: str) -> WakeMatch:
        if not text:
            return WakeMatch(False)
        norm = normalize(text)
        for kw in self._keywords:
            idx = norm.find(kw)
            if idx == -1:
                continue
            remainder = norm[idx + len(kw):]
            remainder = _PUNCT.sub(" ", remainder).strip()
            return WakeMatch(
                matched=True,
                keyword=kw,
                raw_text=text,
                normalized_text=norm,
                remainder=remainder,
            )
        return WakeMatch(False, raw_text=text, normalized_text=norm)
