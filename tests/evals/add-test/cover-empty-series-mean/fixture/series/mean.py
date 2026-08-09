"""Arithmetic mean with a defined answer for an empty series."""

from __future__ import annotations


def mean(values: list[float]) -> float:
    """Mean of ``values``; an empty series has a mean of 0.0, not an error."""
    if not values:
        return 0.0
    return sum(values) / len(values)
