"""A store that keeps everything in a dict. Used by tests and dry runs."""

from __future__ import annotations

from typing import Any


class MemoryStore:
    """Non-persistent key/value store."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def put(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self) -> list[str]:
        return sorted(self._data)
