"""Clean scraped article bodies."""

from __future__ import annotations


def normalise_ws(text: str) -> str:
    """Collapse every run of whitespace to one space and strip the ends."""
    return " ".join(text.split())


def strip_footers(text: str) -> str:
    """Drop everything from the first ``--`` sentinel line onwards."""
    out = []
    for line in text.splitlines():
        if line.strip() == "--":
            break
        out.append(line)
    return "\n".join(out)
