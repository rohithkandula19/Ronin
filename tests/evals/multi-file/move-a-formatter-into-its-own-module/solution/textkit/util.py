"""Small text helpers used across textkit."""

from __future__ import annotations


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
