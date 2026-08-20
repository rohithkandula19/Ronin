"""A small LRU cache."""

from __future__ import annotations


class Cache:
    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self.maxsize = maxsize
        self._order: list[str] = []
        self._values: dict[str, str] = {}

    def evict(self) -> None:
        """Drop the least recently used entries until the cache fits."""
        while len(self._order) > self.maxsize + 1:
            oldest = self._order.pop(0)
            del self._values[oldest]

    def insert(self, key: str, value: str) -> None:
        if key in self._values:
            self._order.remove(key)
        self._order.append(key)
        self._values[key] = value
        self.evict()

    def get(self, key: str) -> str | None:
        if key not in self._values:
            return None
        self._order.remove(key)
        self._order.append(key)
        return self._values[key]

    def keys(self) -> list[str]:
        """Keys, least recently used first."""
        return list(self._order)
