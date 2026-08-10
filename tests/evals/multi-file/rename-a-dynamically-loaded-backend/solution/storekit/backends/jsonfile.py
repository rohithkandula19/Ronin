"""A store backed by a single JSON document on disk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FileStore:
    """Key/value store persisted to *path* as one JSON object.

    The whole document is rewritten on every write, which is fine for the
    handful of keys this is used for.
    """

    def __init__(self, path: str | Path, indent: int = 2) -> None:
        self.path = Path(path)
        self.indent = indent
        self._data: dict[str, Any] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def put(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._write()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self) -> list[str]:
        return sorted(self._data)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data, indent=self.indent, sort_keys=True)
        self.path.write_text(payload + "\n", encoding="utf-8")
