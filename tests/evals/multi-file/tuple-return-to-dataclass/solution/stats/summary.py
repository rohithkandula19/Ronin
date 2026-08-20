"""Reduce a series of samples to three numbers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Summary:
    count: int
    total: float
    mean: float


def summarise(values: list[float]) -> Summary:
    """Return the summary; mean is 0.0 for an empty series."""
    count = len(values)
    total = float(sum(values))
    mean = total / count if count else 0.0
    return Summary(count=count, total=total, mean=mean)
