"""Find probably-unused code — `ronin deadcode`.

A dependency-free dead-code heuristic: collect every public top-level function and
class, then count how often each name is referenced across the whole repo
(excluding its own definition line and import statements). Names that are never
referenced are *candidates* for removal. It deliberately skips things that look
dead but aren't — decorated functions (CLI commands, fixtures, routes), names in
``__all__``, ``main``, and test functions — and labels the rest as candidates to
verify, never as certainties (dynamic dispatch and string lookups can't be seen).
"""
from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin", ".mypy_cache"}
_KEEP_NAMES = {"main", "setup"}
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")


@dataclass
class DeadSymbol:
    name: str
    kind: str          # "function" | "class"
    file: str
    line: int
    decorated: bool = False


def _exported_names(tree: ast.Module) -> set[str]:
    """Names listed in a module-level ``__all__`` (treated as public API). Pure."""
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ) and isinstance(node.value, (ast.List, ast.Tuple)):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    out.add(elt.value)
    return out


def definitions_in_source(source: str, rel: str) -> list[DeadSymbol]:
    """Public top-level functions/classes, with whether they're decorated and
    whether they're in ``__all__`` (those are filtered by the caller). Pure.

    Test functions (``test_*``) and ``main``/``setup`` are skipped outright."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    exported = _exported_names(tree)
    out: list[DeadSymbol] = []
    for node in tree.body:                          # top-level only
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        name = node.name
        if name.startswith("_") or name.startswith("test_") or name in _KEEP_NAMES:
            continue
        if name in exported:                        # part of the public API
            continue
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        out.append(DeadSymbol(name, kind, rel, node.lineno,
                              decorated=bool(node.decorator_list)))
    return out


def _name_counts(sources: list[tuple[str, str]]) -> Counter:
    """Count identifier-token occurrences across all sources, excluding import
    lines (a bare import shouldn't count as 'used'). Pure."""
    counts: Counter = Counter()
    for _rel, src in sources:
        for line in src.splitlines():
            s = line.lstrip()
            if s.startswith("import ") or s.startswith("from "):
                continue
            counts.update(_IDENT_RE.findall(line))
    return counts


def find_dead(root: Path | str, *, include_decorated: bool = False,
              limit: int = 100) -> list[DeadSymbol]:
    """Public symbols whose name never appears outside their own definition line.

    By default decorated functions/classes are excluded (they're usually entry
    points a framework calls — CLI commands, fixtures, routes). Pure given tree."""
    rp = Path(root).resolve()
    sources: list[tuple[str, str]] = []
    defs: dict[str, list[DeadSymbol]] = {}
    for p in rp.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        rel = str(p.relative_to(rp))
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        sources.append((rel, src))
        for d in definitions_in_source(src, rel):
            defs.setdefault(d.name, []).append(d)

    counts = _name_counts(sources)
    dead: list[DeadSymbol] = []
    for name, locs in defs.items():
        # each definition contributes exactly one self-occurrence (the def line);
        # any count beyond that means the name is referenced somewhere.
        if counts.get(name, 0) > len(locs):
            continue
        for d in locs:
            if d.decorated and not include_decorated:
                continue
            dead.append(d)
    dead.sort(key=lambda d: (d.file, d.line))
    return dead[:limit]
