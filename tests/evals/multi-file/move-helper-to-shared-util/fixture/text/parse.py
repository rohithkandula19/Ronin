"""Split a cleaned body into fields."""

from __future__ import annotations

from text.clean import normalise_ws


def parse_headline(text: str) -> str:
    first = text.splitlines()[0] if text.splitlines() else ""
    return normalise_ws(first)
