"""Code-smell detector — `ronin smell`.

A pure, AST-based quality gate in the same "objective, CI-friendly" spirit as
`guard`/`mutants`/`radius`/`flake`. It flags structural smells that correlate
with bugs and review pain, with no LLM and no network:

1. **long-function**   — a function body over N statements.
2. **too-many-params** — a signature with more than N parameters.
3. **deep-nesting**    — control flow nested deeper than N levels.
4. **broad-except**    — `except:` / `except Exception:` (swallows everything).
5. **many-returns**    — more than N `return` statements in one function.
6. **many-locals**     — more than N distinct local names assigned in a function.

The analysis is pure (stdlib ``ast``) and unit-tested. CI-friendly: the command
exits non-zero when any "high"-severity smell is found.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

# Default thresholds — tunable from the CLI.
MAX_STATEMENTS = 50
MAX_PARAMS = 6
MAX_NESTING = 4
MAX_RETURNS = 6
MAX_LOCALS = 15

# Smells at or above this severity fail CI.
HIGH = "high"
WARN = "warn"


@dataclass(frozen=True)
class Smell:
    """One detected smell."""

    kind: str
    severity: str
    function: str
    line: int
    message: str


@dataclass
class Thresholds:
    """Tunable limits; defaults match the module constants."""

    max_statements: int = MAX_STATEMENTS
    max_params: int = MAX_PARAMS
    max_nesting: int = MAX_NESTING
    max_returns: int = MAX_RETURNS
    max_locals: int = MAX_LOCALS


_NESTING_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)
_FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _max_depth(node: ast.AST, depth: int = 0) -> int:
    """Deepest nesting of control-flow statements within ``node``. Pure."""
    best = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _FUNC_NODES):
            continue  # nested functions are analysed on their own
        child_depth = depth + 1 if isinstance(child, _NESTING_NODES) else depth
        best = max(best, _max_depth(child, child_depth))
    return best


def _count(node: ast.AST, types: tuple) -> int:
    """Count descendant nodes of the given types, not crossing nested funcs."""
    total = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _FUNC_NODES):
            continue
        if isinstance(child, types):
            total += 1
        total += _count(child, types)
    return total


def _param_count(fn: ast.AST) -> int:
    a = fn.args
    return len(a.posonlyargs) + len(a.args) + len(a.kwonlyargs) + bool(a.vararg) + bool(a.kwarg)


def _local_names(fn: ast.AST) -> set[str]:
    """Names assigned (= local) within the function body. Pure."""
    names: set[str] = set()
    for child in ast.walk(fn):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            names.add(child.id)
    return names


def _analyse_function(fn: ast.AST, t: Thresholds) -> list[Smell]:
    smells: list[Smell] = []
    name = fn.name
    line = fn.lineno

    statements = _count(fn, (ast.stmt,))
    if statements > t.max_statements:
        smells.append(Smell("long-function", WARN, name, line,
                            f"{statements} statements (> {t.max_statements})"))

    params = _param_count(fn)
    if params > t.max_params:
        smells.append(Smell("too-many-params", WARN, name, line,
                            f"{params} parameters (> {t.max_params})"))

    depth = _max_depth(fn)
    if depth > t.max_nesting:
        smells.append(Smell("deep-nesting", HIGH, name, line,
                            f"nested {depth} levels deep (> {t.max_nesting})"))

    returns = _count(fn, (ast.Return,))
    if returns > t.max_returns:
        smells.append(Smell("many-returns", WARN, name, line,
                            f"{returns} return statements (> {t.max_returns})"))

    locals_count = len(_local_names(fn))
    if locals_count > t.max_locals:
        smells.append(Smell("many-locals", WARN, name, line,
                            f"{locals_count} local names (> {t.max_locals})"))

    return smells


def _broad_excepts(tree: ast.AST) -> list[Smell]:
    """Bare `except:` and `except Exception:` handlers anywhere. Pure."""
    smells: list[Smell] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            t = node.type
            if t is None:
                smells.append(Smell("broad-except", HIGH, "<module>", node.lineno,
                                    "bare `except:` swallows everything"))
            elif isinstance(t, ast.Name) and t.id == "Exception":
                smells.append(Smell("broad-except", WARN, "<module>", node.lineno,
                                    "`except Exception:` is very broad"))
    return smells


def detect_smells(source: str, thresholds: Thresholds | None = None) -> list[Smell]:
    """Detect code smells in Python ``source``. Pure.

    Returns a line-sorted list of :class:`Smell`. On a syntax error returns a
    single ``parse-error`` smell rather than raising.
    """
    t = thresholds or Thresholds()
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [Smell("parse-error", HIGH, "<module>", e.lineno or 0, str(e))]

    smells: list[Smell] = []
    for node in ast.walk(tree):
        if isinstance(node, _FUNC_NODES):
            smells.extend(_analyse_function(node, t))
    smells.extend(_broad_excepts(tree))
    return sorted(smells, key=lambda s: (s.line, s.kind))
