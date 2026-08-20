"""The only consumer of the storage halves."""

from __future__ import annotations

from store.db import Reader, Writer


class Service:
    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.reader = Reader(self.rows)
        self.writer = Writer(self.rows)

    def set_and_read(self, key: str, value: str) -> str | None:
        self.writer.put(key, value)
        return Reader(self.rows).get(key)
