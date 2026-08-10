"""Chunked write into a list sink, so tests need no filesystem."""

from __future__ import annotations

from chunked.limits import CHUNK_BYTES


def write_all(sink: list[bytes], data: bytes) -> int:
    written = 0
    for start in range(0, len(data), CHUNK_BYTES):
        piece = data[start : start + CHUNK_BYTES]
        sink.append(piece)
        written += len(piece)
    return written
