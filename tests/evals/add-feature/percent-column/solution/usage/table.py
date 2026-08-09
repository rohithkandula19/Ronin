"""Render a bucket histogram for the summary pane."""

from __future__ import annotations


def render(buckets: list[tuple[str, int]]) -> list[str]:
    """One line per bucket, in the order given."""
    total = sum(count for _, count in buckets)
    out = []
    for name, count in buckets:
        share = (count * 100.0 / total) if total else 0.0
        out.append("%s %d %.1f%%" % (name, count, share))
    return out
