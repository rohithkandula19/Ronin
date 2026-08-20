"""Order entries by score, stably.

The report this feeds is diffed between runs; an unstable tie-break makes
every run look like a change.
"""

from __future__ import annotations


def order(entries: list[tuple[str, int]]) -> list[str]:
    """Names sorted by descending score, ties in input order."""
    return [name for name, _ in sorted(entries, key=lambda item: -item[1])]
