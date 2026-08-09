"""Reduce a series of samples to three numbers."""

from __future__ import annotations


def summarise(values: list[float]) -> tuple[int, float, float]:
    """Return ``(count, total, mean)``; mean is 0.0 for an empty series."""
    count = len(values)
    total = float(sum(values))
    mean = total / count if count else 0.0
    return count, total, mean
