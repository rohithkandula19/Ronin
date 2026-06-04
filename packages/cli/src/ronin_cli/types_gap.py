"""Find (and add) missing type hints — `ronin types`.

Uses ``ast`` to find public functions whose parameters or return type aren't
annotated, so ronin can add precise hints (test-gated, in isolation). Detection
is pure and unit-tested.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin"}


@dataclass
class TypeGap:
    symbol: str
    file: str
    line: int
    missing: str   # "params" | "return" | "params+return"


def _fn_gap(fn, qual: str, rel: str) -> "TypeGap | None":
    # params (ignore self/cls and *args/**kwargs without annotation is still a gap,
    # but we only flag named positional/keyword params)
    params = [a for a in fn.args.args if a.arg not in ("self", "cls")]
    params += list(fn.args.kwonlyargs)
    missing_params = any(a.annotation is None for a in params)
    missing_return = fn.returns is None and fn.name != "__init__"
    if not (missing_params or missing_return):
        return None
    which = ("params+return" if missing_params and missing_return
             else "params" if missing_params else "return")
    return TypeGap(qual, rel, fn.lineno, which)


def untyped_in_source(source: str, rel: str = "") -> list[TypeGap]:
    """Public functions/methods in ``source`` missing param or return types. Pure."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    gaps: list[TypeGap] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            if (g := _fn_gap(node, node.name, rel)):
                gaps.append(g)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if (isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and not sub.name.startswith("_")):
                    if (g := _fn_gap(sub, f"{node.name}.{sub.name}", rel)):
                        gaps.append(g)
    return gaps


def find_type_gaps(root: Path | str, *, limit: int = 60) -> list[TypeGap]:
    rp = Path(root).resolve()
    out: list[TypeGap] = []
    for p in rp.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        if p.name.startswith("test_") or p.name.endswith("_test.py"):
            continue
        try:
            out.extend(untyped_in_source(p.read_text(encoding="utf-8", errors="ignore"),
                                         str(p.relative_to(rp))))
        except OSError:
            continue
    return out[:limit]


def to_tasks(gaps: list[TypeGap]):
    from .nightshift import Task
    by_file: dict[str, list[TypeGap]] = {}
    for g in gaps:
        by_file.setdefault(g.file, []).append(g)
    return [
        Task("types", f"add type hints in {f}",
             f"Add precise type hints to these under-annotated functions in {f}: "
             + ", ".join(g.symbol for g in gs) +
             ". Infer from usage; import from typing as needed. Don't change behaviour; "
             "keep tests passing.", ref=f)
        for f, gs in by_file.items()
    ]
