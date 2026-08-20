"""The hosts command."""

from __future__ import annotations

from inventory.data import HOSTS


def main(argv: list[str]) -> int:
    for host in HOSTS:
        print(host)
    return 0
