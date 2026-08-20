"""Decide whether a series is worth waking someone up for."""

from __future__ import annotations

from stats.summary import summarise


def should_alert(values: list[float], threshold: float) -> bool:
    total, count, mean = summarise(values)
    return bool(count) and mean > threshold
