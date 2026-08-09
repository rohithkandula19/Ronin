"""Wrap prose for a fixed-width terminal pane."""

from __future__ import annotations


def wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap. No returned line exceeds ``width`` characters."""
    if width <= 0:
        raise ValueError("width must be positive")
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = word if not current else current + " " + word
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
