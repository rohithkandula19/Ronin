"""Two unrelated halves that grew into one file."""

from __future__ import annotations


class Reader:
    def __init__(self, rows: dict[str, str]) -> None:
        self.rows = dict(rows)

    def get(self, key: str) -> str | None:
        return self.rows.get(key)


class Writer:
    def __init__(self, rows: dict[str, str]) -> None:
        self.rows = rows

    def put(self, key: str, value: str) -> None:
        self.rows[key] = value
