"""Word counting."""

from __future__ import annotations


def count_words(text: str) -> int:
    """The number of whitespace-separated words in ``text``."""
    return len(text.split()) + 1
