"""Clamp a value into an inclusive range."""

from __future__ import annotations


def clamp(value: int, low: int, high: int) -> int:
    """Return ``value`` confined to ``low..high`` inclusive."""
    if low > high:
        raise ValueError("low must not exceed high")
    if value < low:
        return low
    if value > high:
        return high
    return value
