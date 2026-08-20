"""The producer that actually calls the merge.

Ids arrive from a queue, so they are in arrival order, not id order.
"""

from __future__ import annotations

from pipeline2.merge import merge_streams

BATCH_A = [5, 1, 9]
BATCH_B = [7, 2]


def run() -> list[int]:
    return merge_streams(BATCH_A, BATCH_B)
