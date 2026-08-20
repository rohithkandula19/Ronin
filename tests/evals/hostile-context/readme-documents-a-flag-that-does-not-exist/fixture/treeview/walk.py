"""Produce the tree lines for a fixed, in-memory tree.

The tree is a literal so the command has deterministic output and needs no
filesystem.
"""

from __future__ import annotations

TREE = {
    "src": {"app.py": None, "lib": {"util.py": None}},
    "README.md": None,
}


def lines(tree: dict[str, object], depth: int | None, level: int = 1) -> list[str]:
    if depth is not None and level > depth:
        return []
    out = []
    for name in sorted(tree):
        out.append("  " * (level - 1) + name)
        child = tree[name]
        if isinstance(child, dict):
            out.extend(lines(child, depth, level + 1))
    return out
