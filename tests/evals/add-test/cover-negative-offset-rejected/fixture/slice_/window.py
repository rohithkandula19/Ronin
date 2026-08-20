"""Take a window out of a list.

A negative offset must be an error: python would happily index from the end
of the list and return a window nobody asked for.
"""

from __future__ import annotations


def window(items: list[str], offset: int, size: int) -> list[str]:
    if offset < 0:
        raise ValueError("offset must not be negative, got %d" % offset)
    if size < 0:
        raise ValueError("size must not be negative, got %d" % size)
    return items[offset : offset + size]
