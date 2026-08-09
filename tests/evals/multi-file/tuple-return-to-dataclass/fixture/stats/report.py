"""One-line human summary."""

from __future__ import annotations

from stats.summary import summarise


def render(values: list[float]) -> str:
    count, total, mean = summarise(values)
    return "n=%d total=%.1f mean=%.2f" % (count, total, mean)
