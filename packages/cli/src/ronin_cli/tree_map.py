"""A clean, annotated project map — `ronin tree`.

Plain `tree` dumps every file including junk. This shows the shape that matters:
directories with how many code files and lines each holds, noise folders skipped,
optionally capped in depth — so you grok a repo's structure in one screen. The
tree building and rendering are pure and unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin", ".mypy_cache", ".idea", ".vscode",
              "egg-info", ".eggs", "htmlcov", ".tox"}
_CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
                  ".rb", ".c", ".cpp", ".h", ".sh", ".lua"}


@dataclass
class Node:
    name: str
    is_dir: bool
    files: int = 0          # code files in this subtree
    lines: int = 0          # code lines in this subtree
    children: list["Node"] = field(default_factory=list)


def _count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def build_tree(root: Path | str, *, max_depth: int = 3) -> Node:
    """Build an annotated directory tree (code files + lines per subtree), pruning
    noise dirs and stopping at ``max_depth``. Pure given the filesystem."""
    rp = Path(root).resolve()

    def walk(d: Path, depth: int) -> Node:
        node = Node(name=d.name or str(d), is_dir=True)
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return node
        for entry in entries:
            if entry.is_dir():
                if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                    continue
                child = walk(entry, depth + 1)
                node.files += child.files
                node.lines += child.lines
                # only attach as a visible child within the depth budget
                if depth < max_depth:
                    node.children.append(child)
            elif entry.is_file() and entry.suffix in _CODE_SUFFIXES:
                node.files += 1
                node.lines += _count_lines(entry)
        return node

    return walk(rp, 0)


def render_lines(node: Node, *, prefix: str = "", is_last: bool = True,
                 is_root: bool = True) -> list[str]:
    """Render a tree to a list of display strings (├──/└── connectors). Pure."""
    out: list[str] = []
    if is_root:
        out.append(f"{node.name}/  ({node.files} files · {node.lines:,} lines)")
    else:
        conn = "└── " if is_last else "├── "
        label = f"{node.name}/" if node.is_dir else node.name
        meta = f"  [{node.files} files · {node.lines:,} lines]" if node.is_dir else ""
        out.append(f"{prefix}{conn}{label}{meta}")
    kids = node.children
    child_prefix = prefix + ("    " if (is_last or is_root) else "│   ")
    if is_root:
        child_prefix = ""
    for i, child in enumerate(kids):
        out.extend(render_lines(child, prefix=child_prefix,
                                is_last=(i == len(kids) - 1), is_root=False))
    return out


def biggest_dirs(node: Node, *, limit: int = 5) -> list[tuple[str, int]]:
    """The directories holding the most lines (recursive), for a quick read of
    where a repo's weight sits. Pure."""
    acc: list[tuple[str, int]] = []

    def visit(n: Node) -> None:
        for c in n.children:
            if c.is_dir:
                acc.append((c.name, c.lines))
                visit(c)

    visit(node)
    acc.sort(key=lambda kv: -kv[1])
    return acc[:limit]
