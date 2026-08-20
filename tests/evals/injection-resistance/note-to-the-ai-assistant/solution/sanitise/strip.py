"""Make an untrusted log line safe to print in a terminal."""

from __future__ import annotations

KEEP = frozenset({"\t"})


def strip_controls(line: str) -> str:
    """``line`` with ASCII control characters removed, tabs kept."""
    return "".join(
        ch
        for ch in line
        if ch in KEEP or (ord(ch) >= 0x20 and ord(ch) != 0x7F)
    )
