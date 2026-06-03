"""Guided migrations — `ronin migrate "<change>" --match <substr>`.

Big mechanical migrations (requests→httpx, callbacks→async, deprecated API → new)
are tedious and risky. ronin finds every file that needs the change, then works
each ONE in an isolated worktree, proving it against the test suite, and saves a
reviewable patch per file — so a sweeping refactor lands as a stack of small,
test-passing, reviewable diffs instead of one scary commit.

Target discovery is pure and unit-tested; the per-file work reuses Nightshift's
sandboxed, test-gated executor.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin"}


@dataclass
class Target:
    path: str
    hits: int   # how many times the match substring appears


def find_targets(root: Path | str, *, glob: str = "*", contains: str | None = None,
                 limit: int = 50) -> list[Target]:
    """Files matching ``glob`` (and containing ``contains``, if given), ranked by
    hit count. Skips vendored/build dirs. Pure given the tree."""
    root_path = Path(root).resolve()
    out: list[Target] = []
    for p in root_path.rglob("*"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file():
            continue
        rel = str(p.relative_to(root_path))
        if not (fnmatch.fnmatch(p.name, glob) or fnmatch.fnmatch(rel, glob)):
            continue
        if contains:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            hits = text.count(contains)
            if hits == 0:
                continue
            out.append(Target(rel, hits))
        else:
            out.append(Target(rel, 0))
    out.sort(key=lambda t: t.hits, reverse=True)
    return out[:limit]


def to_tasks(change: str, targets: list[Target]):
    """Turn the migration + targets into Nightshift Tasks (one per file)."""
    from .nightshift import Task
    return [
        Task("migrate", f"{change} — in {t.path}",
             f"Apply this migration to the file {t.path}: {change}\n"
             "Change ONLY this file. Keep behaviour identical; update/keep tests passing.",
             ref=t.path)
        for t in targets
    ]
