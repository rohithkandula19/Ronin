"""The only module that changes the filesystem.

Commands call these methods so that everything a run did is in one list. Nothing
here knows about options or reports: it does what it is told, and records it.
"""

from __future__ import annotations

from pathlib import Path


class FileOps:
    def __init__(self) -> None:
        self.performed: list[str] = []

    def delete(self, path: Path) -> None:
        path.unlink()
        self.performed.append(f"delete {path}")

    def move(self, source: Path, target: Path) -> None:
        source.rename(target)
        self.performed.append(f"move {source} -> {target}")

    def create_empty(self, path: Path) -> None:
        path.touch()
        self.performed.append(f"create {path}")

    def count(self) -> int:
        return len(self.performed)
