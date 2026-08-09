"""Decide which scratch files to delete. The remover is injected."""

from __future__ import annotations

from typing import Callable


def prune(
    paths: list[str], remover: Callable[[str], None], *, dry_run: bool = False
) -> list[str]:
    """Remove every ``.tmp`` path and return what was (or would be) removed."""
    chosen = sorted(path for path in paths if path.endswith(".tmp"))
    if not dry_run:
        for path in chosen:
            remover(path)
    return chosen
