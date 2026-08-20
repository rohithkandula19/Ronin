"""Decide which scratch files to delete. The remover is injected."""

from __future__ import annotations

from typing import Callable


def prune(paths: list[str], remover: Callable[[str], None]) -> list[str]:
    """Remove every ``.tmp`` path and return what was removed."""
    removed = []
    for path in paths:
        if path.endswith(".tmp"):
            remover(path)
            removed.append(path)
    return removed
