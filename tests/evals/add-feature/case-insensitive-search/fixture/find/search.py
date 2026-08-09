"""Substring search over already-read lines."""

from __future__ import annotations


def find(lines: list[str], needle: str) -> list[str]:
    """Lines containing ``needle``, in input order."""
    return [line for line in lines if needle in line]
