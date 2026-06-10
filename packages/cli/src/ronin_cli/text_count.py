"""Count lines/words/characters/bytes of text — `ronin count` (a friendly wc)."""
from __future__ import annotations


def count_text(text: str) -> dict:
    """Line/word/char/byte counts for text. Pure."""
    return {
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "characters": len(text),
        "bytes": len(text.encode("utf-8")),
    }
