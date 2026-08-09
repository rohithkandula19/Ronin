"""Copy through a bounded buffer."""

from __future__ import annotations

from chunked.limits import CHUNK_BYTES
from chunked.reader import chunks


def copy(data: bytes, sink: list[bytes]) -> int:
    total = 0
    for chunk in chunks(data):
        assert len(chunk) <= CHUNK_BYTES
        sink.append(chunk)
        total += len(chunk)
    return total
