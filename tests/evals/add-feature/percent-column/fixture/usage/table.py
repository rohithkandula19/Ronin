"""Render a bucket histogram for the summary pane."""

from __future__ import annotations


def render(buckets: list[tuple[str, int]]) -> list[str]:
    """One line per bucket, in the order given."""
    return ["%s %d" % (name, count) for name, count in buckets]
