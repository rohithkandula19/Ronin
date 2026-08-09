"""Bound the size of tool output before it reaches a reader."""

from __future__ import annotations


def truncate(text: str, keep: int = 50) -> str:
    """Return at most ``keep`` lines of ``text`` plus a marker naming the cut."""
    lines = text.splitlines()
    total = len(lines)
    if total <= keep:
        return text
    dropped = total - keep
    kept = lines[:keep]
    kept.append("[truncated: %d of %d lines omitted]" % (dropped, total))
    return "\n".join(kept)
