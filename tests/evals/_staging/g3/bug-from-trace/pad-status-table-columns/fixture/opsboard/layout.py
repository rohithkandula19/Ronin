"""Low-level text layout primitives.

Everything here is deliberately dumb: it trusts its arguments and does the
arithmetic. Callers are responsible for handing over sane values.
"""

from __future__ import annotations

ELLIPSIS = "..."


def truncate(text: str, width: int) -> str:
    """Shorten ``text`` so it fits in ``width`` columns, marking the cut."""
    if len(text) <= width:
        return text
    if width <= len(ELLIPSIS):
        return text[:width]
    return text[: width - len(ELLIPSIS)] + ELLIPSIS


def pad_cell(text: str, width: int) -> str:
    """Left-align ``text`` in a field exactly ``width`` columns wide."""
    clipped = truncate(text, width)
    padding = " " * (width - len(clipped))
    return clipped + padding


def rule(width: int, count: int, joiner: str) -> str:
    """A horizontal rule matching a row of ``count`` cells of ``width``."""
    return joiner.join(["-" * width] * count)
