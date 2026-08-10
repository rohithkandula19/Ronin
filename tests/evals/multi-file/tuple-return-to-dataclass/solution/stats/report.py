"""One-line human summary."""

from __future__ import annotations

from stats.summary import summarise


def render(values: list[float]) -> str:
    s = summarise(values)
    return "n=%d total=%.1f mean=%.2f" % (s.count, s.total, s.mean)
