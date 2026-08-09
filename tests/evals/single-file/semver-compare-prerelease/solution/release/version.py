"""Compare the version strings in our release gate."""

from __future__ import annotations


def _core(text: str) -> tuple[int, int, int]:
    head = text.split("-", 1)[0]
    major, minor, patch = (int(part) for part in head.split("."))
    return major, minor, patch


def _pre(text: str) -> str:
    _, sep, rest = text.partition("-")
    return rest if sep else ""


def compare(left: str, right: str) -> int:
    """-1 if ``left`` is older, 0 if equal, 1 if newer."""
    a, b = _core(left), _core(right)
    if a < b:
        return -1
    if a > b:
        return 1
    pa, pb = _pre(left), _pre(right)
    if pa == pb:
        return 0
    if pa == "":
        return 1
    if pb == "":
        return -1
    return -1 if pa < pb else 1
