"""Read half of the store."""

from __future__ import annotations


class Reader:
    def __init__(self, rows: dict[str, str]) -> None:
        self.rows = dict(rows)

    def get(self, key: str) -> str | None:
        return self.rows.get(key)
