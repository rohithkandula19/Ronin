"""Substring search over already-read lines."""

from __future__ import annotations


def find(lines: list[str], needle: str, *, fold_case: bool = False) -> list[str]:
    """Lines containing ``needle``, in input order."""
    if not fold_case:
        return [line for line in lines if needle in line]
    folded = needle.lower()
    return [line for line in lines if folded in line.lower()]
