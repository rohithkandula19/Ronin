"""Slice a result set into pages and describe each page's bounds.

The final page is usually short. ``bounds`` reports the first and last item
actually on the page, so a short final page must report its own real last
item.
"""

from __future__ import annotations


def page(items: list[str], number: int, size: int) -> list[str]:
    if number < 1 or size < 1:
        raise ValueError("number and size must be positive")
    start = (number - 1) * size
    return items[start : start + size]


def bounds(items: list[str], number: int, size: int) -> tuple[str, str]:
    window = page(items, number, size)
    if not window:
        raise ValueError("page %d is empty" % number)
    return window[0], window[-1]
