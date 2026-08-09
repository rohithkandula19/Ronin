"""How a run is divided up.

These helpers are pure arithmetic on counts - they never touch the rows
themselves, so both the progress display and the batching loop can call them
before anything is read.
"""

from __future__ import annotations

DEFAULT_CHUNK_SIZE = 4


def chunk_count(total: int, size: int = DEFAULT_CHUNK_SIZE) -> int:
    """Number of chunks needed to cover ``total`` rows ``size`` at a time."""
    if size <= 0:
        raise ValueError(f"chunk size must be positive, got {size}")
    if total <= 0:
        return 0
    return -(-total // size)


def chunk_start(index: int, size: int = DEFAULT_CHUNK_SIZE) -> int:
    """First row offset of chunk ``index``."""
    return index * size


def describe_split(total: int, size: int = DEFAULT_CHUNK_SIZE) -> str:
    return f"{total} row(s) in {chunk_count(total, size)} chunk(s) of {size}"
