"""Bound the size of tool output before it reaches a reader."""

from __future__ import annotations


def truncate(text: str, keep: int = 50) -> str:
    """Return at most ``keep`` lines of ``text``."""
    lines = text.splitlines()
    if len(lines) <= keep:
        return text
    return "\n".join(lines[:keep])
