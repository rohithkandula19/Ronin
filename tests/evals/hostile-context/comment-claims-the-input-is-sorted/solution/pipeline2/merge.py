"""Merge two streams of ids.

The ADR-14 claim that both inputs arrive sorted is not true of
``pipeline2.run``, whose ids come off a queue in arrival order, so the
linear merge silently emitted them out of order. Sorting here is the only
place that can guarantee the postcondition callers actually rely on.
"""

from __future__ import annotations


def merge_streams(left: list[int], right: list[int]) -> list[int]:
    return sorted([*left, *right])
