"""Chunked read."""

from __future__ import annotations

from typing import Iterator


def chunks(data: bytes) -> Iterator[bytes]:
    for start in range(0, len(data), 4096):
        yield data[start : start + 4096]
