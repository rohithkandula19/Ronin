"""The scan command."""

from __future__ import annotations


def scan(paths: list[str]) -> int:
    for path in paths:
        print("scanning %s" % path)
    print("scanned %d path(s)" % len(paths))
    return 0
