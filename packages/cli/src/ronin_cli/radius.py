"""Blast-radius + impact-aware tests — `ronin radius`.

You changed three files — what *else* could break, and which tests actually
exercise it? ronin builds the repo's Python import graph, walks it backwards
from your uncommitted changes to find every module that (transitively) depends
on what you touched (the "blast radius"), and picks out the test modules in that
radius so you can run only the tests that matter. A risk map plus a fast,
targeted feedback loop.

The graph algorithms are pure and unit-tested; only ``build_import_graph`` and
diff collection touch the filesystem.
"""
from __future__ import annotations

import ast
import subprocess
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .code_tools import _SKIP_DIRS
from .guard import changed_files as _changed_files

# Module keys are repo-relative posix paths WITHOUT the .py suffix, e.g.
# "pkg/sub/mod". A package is keyed by its directory ("pkg/sub" for its __init__).


def parse_imports(source: str) -> list[tuple[str, int]]:
    """Return (dotted_target, relative_level) for each import in ``source``.
    level 0 = absolute (``import a.b`` / ``from a.b import c``); level>=1 =
    relative (``from . import x`` / ``from ..y import z``). Pure."""
    out: list[tuple[str, int]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((a.name, 0) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or "", node.level))
    return out


def _longest_internal(keys: set[str], candidate: str) -> str | None:
    """Map a slash-path ``candidate`` to the longest internal key that is a
    prefix of it (so `from pkg.mod import thing` resolves to `pkg/mod`)."""
    parts = [p for p in candidate.split("/") if p]
    while parts:
        key = "/".join(parts)
        if key in keys:
            return key
        parts.pop()
    return None


def resolve_import(keys: set[str], importer: str, dotted: str, level: int) -> str | None:
    """Resolve one import from module ``importer`` to an internal module key, or
    None if it points outside the repo. Pure — ``keys`` is the set of repo keys."""
    if level == 0:
        return _longest_internal(keys, dotted.replace(".", "/"))
    # relative: climb (level-1) packages up from the importer's own package.
    base_parts = importer.split("/")[:-1]          # importer's package dir
    up = level - 1
    if up > len(base_parts):
        return None
    base_parts = base_parts[: len(base_parts) - up]
    target = "/".join(base_parts + (dotted.replace(".", "/").split("/") if dotted else []))
    return _longest_internal(keys, target)


def reverse_dependents(graph: dict[str, set[str]], changed: set[str]) -> set[str]:
    """Every module that transitively imports any module in ``changed`` (the
    blast radius, excluding the changed set itself). Pure."""
    reverse: dict[str, set[str]] = {}
    for mod, deps in graph.items():
        for d in deps:
            reverse.setdefault(d, set()).add(mod)
    seen: set[str] = set()
    queue = deque(changed)
    while queue:
        cur = queue.popleft()
        for importer in reverse.get(cur, ()):
            if importer not in seen:
                seen.add(importer)
                queue.append(importer)
    return seen - changed


def is_test_module(key: str) -> bool:
    name = key.split("/")[-1]
    return name.startswith("test_") or name.endswith("_test")


@dataclass
class RadiusResult:
    changed: list[str] = field(default_factory=list)
    radius: list[str] = field(default_factory=list)       # dependents (excl. changed)
    affected_tests: list[str] = field(default_factory=list)  # test FILES (paths)
    note: str = ""


def _path_to_key(rel: Path) -> str:
    posix = rel.with_suffix("").as_posix()
    if posix.endswith("/__init__"):
        posix = posix[: -len("/__init__")]
    return posix


def build_import_graph(root: Path | str) -> dict[str, set[str]]:
    """Map every internal Python module key → the internal module keys it imports."""
    root_path = Path(root).resolve()
    files: dict[str, Path] = {}
    for p in root_path.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.relative_to(root_path).parts):
            continue
        files[_path_to_key(p.relative_to(root_path))] = p
    keys = set(files)
    graph: dict[str, set[str]] = {}
    for key, path in files.items():
        try:
            src = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        deps: set[str] = set()
        for dotted, level in parse_imports(src):
            dep = resolve_import(keys, key, dotted, level)
            if dep and dep != key:
                deps.add(dep)
        graph[key] = deps
    return graph


def run_radius(root: Path | str, *, diff: str | None = None) -> RadiusResult:
    """Collect the working diff, build the import graph, and compute the blast
    radius + affected test files for the changed Python modules."""
    root_path = Path(root).resolve()
    if diff is None:
        from .guard import collect_diff
        diff = collect_diff(root_path)
    if not (diff or "").strip():
        return RadiusResult(note="no changes against HEAD")

    changed_keys = {_path_to_key(Path(f)) for f in _changed_files(diff) if f.endswith(".py")}
    if not changed_keys:
        return RadiusResult(note="no Python files changed")

    graph = build_import_graph(root_path)
    known = set(graph) & changed_keys
    radius = reverse_dependents(graph, known)

    test_keys = sorted(k for k in (radius | known) if is_test_module(k))
    return RadiusResult(
        changed=sorted(changed_keys),
        radius=sorted(radius),
        affected_tests=[k + ".py" for k in test_keys],
    )
