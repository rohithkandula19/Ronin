"""Chunked write into a list sink, so tests need no filesystem."""

from __future__ import annotations


def write_all(sink: list[bytes], data: bytes) -> int:
    written = 0
    for start in range(0, len(data), 4096):
        piece = data[start : start + 4096]
        sink.append(piece)
        written += len(piece)
    return written
