"""Cyclomatic complexity, per function, worst first.

The signal :func:`~ronin.repo.analyze.health` does not carry. It reports parse errors,
orphans, oversized surfaces and missing tests — all derived from the scan, with no second
read of the tree, which its docstring states as an invariant. Complexity needs the
function bodies and :class:`~ronin.context.repomap.RepoScan` keeps only signatures, so
this is a separate subcommand rather than a fifth health signal. The invariant stays
true; the reader gets the number.

McCabe's measure: the number of linearly independent paths through a function. One, plus
one for every point the control flow can go two ways. High numbers mean many paths, which
means many tests to cover it and many states to hold in your head while reading it.

Ported from v1's ``ronin1 dev complexity``, with **two counting bugs fixed** — both
confirmed against the shipped code before being changed, and both pinned by tests.

``with`` is not a branch. v1 counted ``With`` and ``AsyncWith`` as decision points, so
``with open(p) as f: return f.read()`` scored 2 where the same logic without a context
manager scored 1. A ``with`` block has exactly one path through it; what it does is bind
and unbind, not choose. Counting it inflates every function that touches a file, a lock
or a transaction, which in modern Python is most of them — and a measure that is high
everywhere ranks nothing.

A nested function's branches are its own. v1 walked each function with :func:`ast.walk`,
which descends into nested ``def``s, so a factory whose inner function has one ``if``
scored 2 despite containing no branch at all — and the inner function was reported at 2
as well. The branch was counted twice and attributed to a function that did not have it.
Here the walk stops at a nested function and that function is measured separately, which
is both the correct attribution and the useful one: the fix belongs in the function that
actually branches.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Final

#: What a score means, worst first. The words matter more than the numbers to most
#: readers, and the boundaries are the conventional ones rather than anything derived:
#: the value of a rating is that everybody reads it the same way.
RATINGS: Final[tuple[tuple[int, str], ...]] = (
    (30, "untestable"),
    (20, "very high"),
    (10, "high"),
    (6, "moderate"),
    (0, "simple"),
)

#: Below this, a function is not worth a line in a report. Ten is where "high" starts.
DEFAULT_THRESHOLD: Final = 10

#: How many functions a report lists. A ranking nobody finishes reading is a ranking
#: that changes nothing.
DEFAULT_LIMIT: Final = 25

#: Nodes that add exactly one path each.
#:
#: ``With`` and ``AsyncWith`` are deliberately absent — see the module docstring.
#: ``Assert`` is present and is the one judgement call here: an assert can raise, so it
#: is a second exit, and a function dense with them really does have more states to
#: reason about.
_BRANCHES: Final = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.IfExp,
    ast.Assert,
    ast.match_case,
)

_FUNCTIONS: Final = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True, slots=True)
class Complexity:
    """One function and how many ways through it there are."""

    name: str
    path: str
    line: int
    score: int

    @property
    def rating(self) -> str:
        return rating(self.score)


def rating(score: int) -> str:
    for floor, label in RATINGS:
        if score >= floor:
            return label
    return RATINGS[-1][1]  # pragma: no cover - the last floor is 0


def _own_nodes(node: ast.AST) -> list[ast.AST]:
    """Every node inside ``node`` that is not inside a *nested* function.

    :func:`ast.walk` cannot express this — it descends into everything — and that is
    exactly the second bug being fixed. A nested ``def`` is a boundary: its branches are
    counted against it, once, and not against whatever happens to enclose it.
    """
    out: list[ast.AST] = []
    stack: list[ast.AST] = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        out.append(current)
        if isinstance(current, (*_FUNCTIONS, ast.Lambda)):
            # A lambda is a separate callable too. Its `a if b else c` is not the
            # enclosing function's branch, and unlike a `def` it has no name to report
            # under — so it is excluded rather than measured.
            continue
        stack.extend(ast.iter_child_nodes(current))
    return out


def cyclomatic(node: ast.AST) -> int:
    """How many linearly independent paths run through one function.

    One, plus one per decision point. Does not descend into nested functions; call it
    on those separately.
    """
    score = 1
    for child in _own_nodes(node):
        if isinstance(child, _BRANCHES):
            score += 1
        elif isinstance(child, ast.BoolOp):
            # `a and b and c` is two operators, so two extra ways to leave early.
            score += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            # The loop itself, plus each `if` clause filtering it.
            score += 1 + len(child.ifs)
    return score


def _qualified(tree: ast.Module) -> dict[int, str]:
    """``id(node) -> "Class.method"`` for methods, so a report can be read without
    opening the file. Only direct children of a class body, because a function defined
    inside a method is not that class's method."""
    names: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, _FUNCTIONS):
                    names[id(child)] = f"{node.name}.{child.name}"
    return names


def complexity_of(source: str, path: str) -> tuple[Complexity, ...]:
    """Every function in ``source``, with its score. Pure: no I/O, no filesystem.

    A file that does not parse yields nothing rather than raising. A complexity report
    is advisory, and one syntax error in a tree should not deny the reader the other
    nine hundred files.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()

    names = _qualified(tree)
    found = [
        Complexity(
            name=names.get(id(node), node.name),
            path=path,
            line=node.lineno,
            score=cyclomatic(node),
        )
        for node in ast.walk(tree)
        if isinstance(node, _FUNCTIONS)
    ]
    return tuple(sorted(found, key=lambda item: (-item.score, item.path, item.line)))


def rank(
    sources: dict[str, str] | list[tuple[str, str]],
    *,
    threshold: int = DEFAULT_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
) -> tuple[Complexity, ...]:
    """The worst ``limit`` functions at or above ``threshold``, across many files.

    Takes the sources rather than a root, so the whole ranking is testable with a dict
    and the tree walk lives in :mod:`ronin.cli.repo` with the rest of the I/O.
    """
    pairs = sources.items() if isinstance(sources, dict) else sources
    found = [
        item
        for path, source in pairs
        for item in complexity_of(source, path)
        if item.score >= threshold
    ]
    found.sort(key=lambda item: (-item.score, item.path, item.line))
    return tuple(found[:limit])


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_THRESHOLD",
    "RATINGS",
    "Complexity",
    "complexity_of",
    "cyclomatic",
    "rank",
    "rating",
]
