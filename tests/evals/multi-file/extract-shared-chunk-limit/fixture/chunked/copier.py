"""Copy through a bounded buffer."""

from __future__ import annotations

from chunked.reader import chunks


def copy(data: bytes, sink: list[bytes]) -> int:
    total = 0
    for chunk in chunks(data):
        assert len(chunk) <= 4096
        sink.append(chunk)
        total += len(chunk)
    return total
