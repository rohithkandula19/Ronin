"""Chunked read."""

from __future__ import annotations

from typing import Iterator

from chunked.limits import CHUNK_BYTES


def chunks(data: bytes) -> Iterator[bytes]:
    for start in range(0, len(data), CHUNK_BYTES):
        yield data[start : start + CHUNK_BYTES]
