"""Whitespace helpers shared by the cleaner and the parser."""

from __future__ import annotations


def normalise_ws(text: str) -> str:
    """Collapse every run of whitespace to one space and strip the ends."""
    return " ".join(text.split())
