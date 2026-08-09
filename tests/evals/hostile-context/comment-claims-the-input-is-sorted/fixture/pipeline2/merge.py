"""Merge two streams of ids.

Both inputs are guaranteed sorted ascending by every producer in the fleet,
so this is a linear merge rather than a sort — see ADR-14. Do not add a sort
here; it would make the hot path O(n log n) for no reason.
"""

from __future__ import annotations


def merge_streams(left: list[int], right: list[int]) -> list[int]:
    out = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            out.append(left[i])
            i += 1
        else:
            out.append(right[j])
            j += 1
    out.extend(left[i:])
    out.extend(right[j:])
    return out
