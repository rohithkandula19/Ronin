"""Re-run a command whenever your code changes — `ronin watch`.

A dependency-free file watcher: snapshot the mtimes of your source files, poll for
changes, and run a command (default: your test suite) every time something is
saved. When the command fails, ronin can optionally hand the failure straight to
the agent to fix it. The change-detection is pure and unit-tested; the loop and
the agent hand-off live in the command.
"""
from __future__ import annotations

from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin", ".mypy_cache"}


def snapshot(root: Path | str, *, suffixes: tuple[str, ...] = (".py",)) -> dict[str, float]:
    """Map every watched source file → its mtime. Pure given the filesystem."""
    rp = Path(root).resolve()
    out: dict[str, float] = {}
    for p in rp.rglob("*"):
        if not p.is_file() or p.suffix not in suffixes:
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        try:
            out[str(p)] = p.stat().st_mtime
        except OSError:
            continue
    return out


def changed(old: dict[str, float], new: dict[str, float]) -> set[str]:
    """Paths that were added, removed, or whose mtime moved. Pure."""
    hits: set[str] = set()
    for path, mtime in new.items():
        if path not in old or old[path] != mtime:
            hits.add(path)
    for path in old:
        if path not in new:                       # deleted
            hits.add(path)
    return hits


def describe_change(paths: set[str], root: Path | str) -> str:
    """Short, human label for what changed (relative paths). Pure."""
    rp = Path(root).resolve()
    rels = []
    for p in sorted(paths):
        try:
            rels.append(str(Path(p).relative_to(rp)))
        except ValueError:
            rels.append(p)
    if not rels:
        return "no files"
    head = ", ".join(rels[:3])
    return head + (f" (+{len(rels) - 3} more)" if len(rels) > 3 else "")
