"""Slices a row list into labelled chunks ready to hand to a worker."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from shard.sizing import DEFAULT_CHUNK_SIZE, chunk_count, chunk_start


@dataclass(frozen=True)
class Chunk:
    index: int
    label: str
    rows: list[dict[str, Any]]

    def summary(self) -> str:
        return f"chunk {self.index} [{self.label}] {len(self.rows)} row(s)"


def take(rows: Sequence[dict[str, Any]], start: int, size: int) -> list[dict[str, Any]]:
    """The rows of one chunk; the final chunk may be short."""
    end = min(start + size, len(rows))
    return [rows[offset] for offset in range(start, end)]


def iter_chunks(
    rows: Sequence[dict[str, Any]], size: int = DEFAULT_CHUNK_SIZE
) -> list[Chunk]:
    chunks = []
    for index in range(chunk_count(len(rows), size)):
        start = chunk_start(index, size)
        first = rows[start]
        chunks.append(
            Chunk(index=index, label=f"from {first['order_id']}", rows=take(rows, start, size))
        )
    return chunks
