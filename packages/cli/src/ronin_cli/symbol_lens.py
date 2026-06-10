"""Deep-dive one symbol — `ronin explain <symbol>`.

Given a function/class name, ronin finds where it's *defined* and every place it's
*used* across the repo (call sites), then the agent explains the contract, the
behaviour, and how callers depend on it. The locating is pure ast/regex work and
unit-tested; the explanation is the agent's read-only synthesis.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin"}


@dataclass
class Definition:
    symbol: str
    kind: str          # "function" | "class" | "method"
    file: str
    line: int


@dataclass
class CallSite:
    file: str
    line: int
    text: str          # the source line, stripped


def definitions_in_source(source: str, symbol: str, rel: str) -> list[Definition]:
    """Every def/class named ``symbol`` in ``source`` (incl. methods). Pure."""
    out: list[Definition] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    # map child→parent so we can tell a method from a top-level function
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            if isinstance(node, ast.ClassDef):
                kind = "class"
            else:
                kind = "method" if isinstance(parents.get(id(node)), ast.ClassDef) else "function"
            out.append(Definition(symbol, kind, rel, node.lineno))
    return out


def references_in_source(source: str, symbol: str, rel: str, *, def_lines: set[int] | None = None) -> list[CallSite]:
    """Lines where ``symbol`` appears as a whole word, excluding its own
    definition lines and import lines. Pure."""
    def_lines = def_lines or set()
    pat = re.compile(rf"\b{re.escape(symbol)}\b")
    out: list[CallSite] = []
    for i, line in enumerate(source.splitlines(), start=1):
        if i in def_lines:
            continue
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            continue
        if pat.search(line):
            out.append(CallSite(rel, i, stripped[:200]))
    return out


def locate(root: Path | str, symbol: str, *, limit: int = 60) -> dict:
    """Find all definitions + call sites of ``symbol`` across the repo. Pure
    given the tree. Returns {'definitions': [...], 'call_sites': [...]}."""
    rp = Path(root).resolve()
    defs: list[Definition] = []
    sites: list[CallSite] = []
    for p in rp.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        rel = str(p.relative_to(rp))
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if symbol not in src:                     # cheap pre-filter
            continue
        file_defs = definitions_in_source(src, symbol, rel)
        defs.extend(file_defs)
        def_lines = {d.line for d in file_defs}
        sites.extend(references_in_source(src, symbol, rel, def_lines=def_lines))
    return {"definitions": defs, "call_sites": sites[:limit], "total_sites": len(sites)}


def explain_brief(loc: dict, symbol: str) -> str:
    """Compact text summary of where the symbol lives + is used. Pure."""
    lines = []
    for d in loc["definitions"]:
        lines.append(f"defined ({d.kind}) at {d.file}:{d.line}")
    n = loc["total_sites"]
    lines.append(f"{n} call site(s)" + (" — sample:" if n else ""))
    for s in loc["call_sites"][:20]:
        lines.append(f"  {s.file}:{s.line}  {s.text}")
    return "\n".join(lines)


def explain_prompt(loc: dict, symbol: str) -> str:
    return (
        f"Explain the symbol `{symbol}` for a developer new to it. Read its "
        "definition and a few call sites, then cover: what it does (one line), its "
        "contract (params, return, side effects, errors), how callers actually use "
        "it, and any subtlety or footgun. Be concrete and tight — don't restate the "
        f"code line by line.\n\nWhere it lives:\n{explain_brief(loc, symbol)}"
    )
