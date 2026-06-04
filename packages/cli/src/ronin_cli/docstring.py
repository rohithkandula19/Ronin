"""Find (and write) missing docstrings — `ronin docstring`.

Walks your Python source with the ``ast`` module and finds public functions and
classes that have NO docstring, then ronin can write one for each (test-gated, in
isolation). The detection is pure and unit-tested.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin"}


@dataclass
class DocGap:
    symbol: str
    kind: str    # "function" | "class"
    file: str
    line: int


def undocumented_in_source(source: str, rel: str = "") -> list[DocGap]:
    """Public top-level + class-level defs/classes in ``source`` with no
    docstring. Pure (parses with ast; returns [] on a syntax error)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    gaps: list[DocGap] = []

    def _check(node, kind):
        name = node.name
        if name.startswith("_"):
            return
        if ast.get_docstring(node) is None:
            gaps.append(DocGap(name, kind, rel, node.lineno))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check(node, "function")
        elif isinstance(node, ast.ClassDef):
            _check(node, "class")
            for sub in node.body:   # public methods
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("_"):
                    if ast.get_docstring(sub) is None:
                        gaps.append(DocGap(f"{node.name}.{sub.name}", "function", rel, sub.lineno))
    return gaps


def find_doc_gaps(root: Path | str, *, limit: int = 60) -> list[DocGap]:
    """Undocumented public symbols across the repo's non-test Python files."""
    rp = Path(root).resolve()
    out: list[DocGap] = []
    for p in rp.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        name = p.name
        if name.startswith("test_") or name.endswith("_test.py"):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out.extend(undocumented_in_source(src, str(p.relative_to(rp))))
    return out[:limit]


def to_tasks(gaps: list[DocGap]):
    from .nightshift import Task
    # group by file so one task documents a whole file (fewer, richer edits)
    by_file: dict[str, list[DocGap]] = {}
    for g in gaps:
        by_file.setdefault(g.file, []).append(g)
    return [
        Task("docstring", f"add docstrings in {f}",
             f"Add clear, concise docstrings to these undocumented symbols in {f}: "
             + ", ".join(g.symbol for g in gs) +
             ". Match the repo's docstring style. Don't change behaviour.", ref=f)
        for f, gs in by_file.items()
    ]
