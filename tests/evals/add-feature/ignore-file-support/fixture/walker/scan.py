"""List the files under a root, deterministically."""

from __future__ import annotations

import os


def scan(root: str) -> list[str]:
    """Every file under ``root``, as sorted ``/``-separated relative paths."""
    found = []
    for base, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(base, name)
            found.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return sorted(found)
