"""Small text helpers used across textkit."""

from __future__ import annotations

import re
import unicodedata

SLUG_SEPARATOR = "-"
SLUG_STOPWORDS = frozenset({"a", "an", "and", "of", "or", "the"})
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def normalize_ws(text: str) -> str:
    """Collapse every run of whitespace in *text* into a single space."""
    return " ".join(text.split())


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    """Shorten *text* to at most *limit* characters, ending with *suffix*."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return text[:limit]
    return text[: limit - len(suffix)].rstrip() + suffix


def _slug_words(text: str) -> list[str]:
    """Split *text* into lowercase ascii words, dropping everything else."""
    folded = unicodedata.normalize("NFKD", text)
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    return [word for word in _SLUG_STRIP_RE.split(ascii_text.lower()) if word]


def slugify(text: str, *, max_words: int = 8) -> str:
    """Build a url-safe slug from *text*.

    Stopwords are dropped unless that would leave nothing behind.
    """
    words = [word for word in _slug_words(text) if word not in SLUG_STOPWORDS]
    if not words:
        words = _slug_words(text)
    return SLUG_SEPARATOR.join(words[:max_words])
