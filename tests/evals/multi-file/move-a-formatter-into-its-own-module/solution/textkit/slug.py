"""Turning titles into url-safe slugs."""

from __future__ import annotations

import re
import unicodedata

SLUG_SEPARATOR = "-"
SLUG_STOPWORDS = frozenset({"a", "an", "and", "of", "or", "the"})
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


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
