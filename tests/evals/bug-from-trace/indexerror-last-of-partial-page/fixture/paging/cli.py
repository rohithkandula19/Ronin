"""Print the bounds of every page."""

from __future__ import annotations

from paging.page import bounds, page

ITEMS = ["a", "b", "c", "d", "e"]
SIZE = 2


def main() -> None:
    number = 1
    while page(ITEMS, number, SIZE):
        first, last = bounds(ITEMS, number, SIZE)
        print("page %d: %s..%s" % (number, first, last))
        number += 1
