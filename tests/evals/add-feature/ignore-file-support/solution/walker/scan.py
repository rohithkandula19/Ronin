"""List the files under a root, deterministically."""

from __future__ import annotations

import fnmatch
import os

IGNORE_FILE = ".scanignore"


def _patterns(root: str) -> list[str]:
    path = os.path.join(root, IGNORE_FILE)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                out.append(stripped)
    return out


def _ignored(relative: str, patterns: list[str]) -> bool:
    base = relative.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(base, pattern)
        for pattern in patterns
    )


def scan(root: str) -> list[str]:
    """Every file under ``root``, as sorted ``/``-separated relative paths."""
    patterns = _patterns(root)
    found = []
    for base, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(base, name)
            relative = os.path.relpath(full, root).replace(os.sep, "/")
            if relative == IGNORE_FILE:
                continue
            if _ignored(relative, patterns):
                continue
            found.append(relative)
    return sorted(found)
