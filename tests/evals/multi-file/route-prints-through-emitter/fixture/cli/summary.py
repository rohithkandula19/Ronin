"""The summary command."""

from __future__ import annotations


def summary(counts: dict[str, int]) -> int:
    for key in sorted(counts):
        print("%s: %d" % (key, counts[key]))
    return 0
