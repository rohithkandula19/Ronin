"""Compare the version strings in our release gate."""

from __future__ import annotations


def _core(text: str) -> tuple[int, int, int]:
    head = text.split("-", 1)[0]
    major, minor, patch = (int(part) for part in head.split("."))
    return major, minor, patch


def compare(left: str, right: str) -> int:
    """-1 if ``left`` is older, 0 if equal, 1 if newer."""
    a, b = _core(left), _core(right)
    if a < b:
        return -1
    if a > b:
        return 1
    return 0
