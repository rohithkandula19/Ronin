"""Find untested code and (optionally) write tests for it — `ronin coverage`.

A lightweight, dependency-free coverage heuristic: collect every public top-level
def/class in your source, then check whether its *name* appears anywhere in your
test files. Names that never show up are likely untested — ronin can then write a
passing test for each (test-gated, in isolation, as reviewable patches). The
detection is pure and unit-tested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_SYM_RE = re.compile(r"^(?:def|class)\s+([A-Za-z]\w*)", re.MULTILINE)
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin"}


@dataclass
class Gap:
    symbol: str
    file: str


def public_symbols(text: str) -> list[str]:
    """Top-level def/class names (excluding _private), in order, de-duped. Pure."""
    seen: list[str] = []
    for m in _SYM_RE.finditer(text or ""):
        name = m.group(1)
        if not name.startswith("_") and name not in seen:
            seen.append(name)
    return seen


def _is_test_file(rel: str) -> bool:
    base = Path(rel).name
    return base.startswith("test_") or base.endswith("_test.py") or "/tests/" in f"/{rel}"


def find_gaps(root: Path | str, *, src_glob: str = "*.py", limit: int = 40) -> list[Gap]:
    """Public symbols in non-test source whose name never appears in any test
    file. A name referenced in tests is assumed covered. Pure given the tree."""
    root_path = Path(root).resolve()
    src_files: list[tuple[str, str]] = []
    test_blob: list[str] = []
    import fnmatch
    for p in root_path.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        rel = str(p.relative_to(root_path))
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _is_test_file(rel):
            test_blob.append(text)
            continue
        if fnmatch.fnmatch(p.name, src_glob):
            src_files.append((rel, text))

    tests = "\n".join(test_blob)
    gaps: list[Gap] = []
    for rel, text in src_files:
        for sym in public_symbols(text):
            # word-boundary check so 'add' doesn't match 'address'
            if not re.search(rf"\b{re.escape(sym)}\b", tests):
                gaps.append(Gap(sym, rel))
    return gaps[:limit]


def to_tasks(gaps: list[Gap]):
    """Turn coverage gaps into Nightshift Tasks (one test per symbol)."""
    from .nightshift import Task
    return [
        Task("coverage", f"add a test for {g.symbol} ({g.file})",
             f"Write a focused unit test for `{g.symbol}` defined in {g.file}. "
             "Put it in the project's tests using existing conventions. The test "
             "must pass and actually exercise the symbol's behaviour.",
             ref=f"{g.file}::{g.symbol}")
        for g in gaps
    ]
