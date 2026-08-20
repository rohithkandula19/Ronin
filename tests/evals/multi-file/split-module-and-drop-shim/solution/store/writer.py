"""Write half of the store."""

from __future__ import annotations


class Writer:
    def __init__(self, rows: dict[str, str]) -> None:
        self.rows = rows

    def put(self, key: str, value: str) -> None:
        self.rows[key] = value
