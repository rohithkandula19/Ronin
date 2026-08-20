"""The recent command."""

from __future__ import annotations

from recent.data import ENTRIES


def main(argv: list[str]) -> int:
    for entry in ENTRIES:
        print(entry)
    return 0
