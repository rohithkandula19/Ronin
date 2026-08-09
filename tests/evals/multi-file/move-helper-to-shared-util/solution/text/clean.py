"""Clean scraped article bodies."""

from __future__ import annotations


def strip_footers(text: str) -> str:
    """Drop everything from the first ``--`` sentinel line onwards."""
    out = []
    for line in text.splitlines():
        if line.strip() == "--":
            break
        out.append(line)
    return "\n".join(out)
