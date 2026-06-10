"""Find your most complex functions — `ronin complexity`.

Cyclomatic complexity counts the independent paths through a function: every
if / for / while / except / boolean operator / comprehension-filter / ternary
adds one to a base of one. High numbers mean many branches — hard to test, hard
to reason about, prime refactor targets. ronin ranks the worst offenders and can
hand the top one to the agent to simplify. Pure ast work, fully unit-tested.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin", ".mypy_cache"}


@dataclass
class FuncComplexity:
    name: str
    file: str
    line: int
    score: int


def cyclomatic(node: ast.AST) -> int:
    """Cyclomatic complexity of a function/method node. Base 1, +1 per decision
    point (branch, loop, except handler, boolean operator, comprehension filter,
    ternary, match case, assert). Pure."""
    score = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While,
                              ast.ExceptHandler, ast.IfExp, ast.Assert, ast.With,
                              ast.AsyncWith)):
            score += 1
        elif isinstance(child, ast.BoolOp):
            # `a and b and c` has 2 operators → 2 extra paths
            score += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            score += 1 + len(child.ifs)
        elif isinstance(child, getattr(ast, "match_case", ())):  # py310+ match
            score += 1
    return score


def complexities_in_source(source: str, rel: str) -> list[FuncComplexity]:
    """Complexity of every function/method in the source. Pure."""
    out: list[FuncComplexity] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    # qualify methods with their class for readability
    class_of: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_of[id(child)] = node.name
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            cls = class_of.get(id(node))
            if cls:
                name = f"{cls}.{name}"
            out.append(FuncComplexity(name, rel, node.lineno, cyclomatic(node)))
    return out


def find_complex(root: Path | str, *, threshold: int = 10, limit: int = 25) -> list[FuncComplexity]:
    """All functions at or above ``threshold`` complexity, worst first. Pure
    given the tree."""
    rp = Path(root).resolve()
    out: list[FuncComplexity] = []
    for p in rp.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        rel = str(p.relative_to(rp))
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out.extend(c for c in complexities_in_source(src, rel) if c.score >= threshold)
    out.sort(key=lambda c: (-c.score, c.file, c.line))
    return out[:limit]


def rating(score: int) -> str:
    """Human label for a complexity score. Pure."""
    if score >= 30:
        return "untestable"
    if score >= 20:
        return "very high"
    if score >= 10:
        return "high"
    if score >= 6:
        return "moderate"
    return "simple"


def refactor_prompt(top: FuncComplexity) -> str:
    return (
        f"The function `{top.name}` in {top.file} (line {top.line}) has a cyclomatic "
        f"complexity of {top.score} — too many branches to test or reason about easily. "
        "Read it, then propose (and implement) a refactor that lowers its complexity "
        "without changing behaviour: extract helpers, flatten nesting, use guard "
        "clauses or table/dispatch. Keep the public signature stable and ensure the "
        "tests still pass."
    )
