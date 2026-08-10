"""Deterministic identifiers: ``note-1``, ``note-2``, ... per store."""

from __future__ import annotations


class Sequence:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self._next = 1

    def take(self) -> str:
        value = f"{self.prefix}-{self._next}"
        self._next += 1
        return value

    @property
    def issued(self) -> int:
        return self._next - 1
