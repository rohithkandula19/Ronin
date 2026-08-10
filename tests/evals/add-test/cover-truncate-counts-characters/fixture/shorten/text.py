"""Shorten a label for a fixed-width column.

Counted in characters, never bytes: a byte slice cuts a multi-byte
character in half and the terminal renders a replacement glyph.
"""

from __future__ import annotations

ELLIPSIS = "..."


def shorten(label: str, limit: int) -> str:
    if limit < len(ELLIPSIS):
        raise ValueError("limit must leave room for the ellipsis")
    if len(label) <= limit:
        return label
    return label[: limit - len(ELLIPSIS)] + ELLIPSIS
