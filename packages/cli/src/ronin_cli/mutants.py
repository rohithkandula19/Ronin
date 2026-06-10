"""Mutation-testing quality gate — `ronin mutants`.

Coverage tells you which lines *ran*; mutation testing tells you whether your
tests would *notice if those lines were wrong*. ronin injects tiny mutations
into a file (``==`` → ``!=``, ``and`` → ``or``, ``True`` → ``False``, …), runs
your suite against each one, and reports the mutants that **survived** — every
survivor is a change your tests failed to catch. Pairs with kaizen's
"tests-as-fitness" philosophy. Mutant generation and scoring are pure and
unit-tested; runs are sandboxed and the original file is always restored.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# (before, after, word-bounded). Spaced operators carry their spaces so the
# replacement is unambiguous; identifier-like tokens (True/False) are bounded so
# we never mutate inside `Truthy`/`is_false`. Listed widest-first (``<=`` before
# ``<``) is unnecessary here since we match exact substrings, but order defines
# the reporting order.
_MUTATION_OPS: list[tuple[str, str, bool]] = [
    ("==", "!=", False),
    ("!=", "==", False),
    # Relational boundary toggles. Spaced so they never touch `->` (no space
    # before `>`) or `>>`/`<<` (no space after the first angle).
    (" <= ", " < ", False),
    (" >= ", " > ", False),
    (" < ", " <= ", False),
    (" > ", " >= ", False),
    (" and ", " or ", False),
    (" or ", " and ", False),
    ("True", "False", True),
    ("False", "True", True),
    (" + ", " - ", False),
    (" - ", " + ", False),
]


@dataclass
class Mutant:
    lineno: int            # 1-based line that was mutated
    label: str             # e.g. "== → !="
    mutated_source: str    # full file content with this single mutation applied
    killed: bool | None = None  # set after a run: True = caught, False = survived


@dataclass
class MutantResult:
    target: str
    total: int = 0
    killed: int = 0
    survivors: list[Mutant] = field(default_factory=list)
    note: str = ""

    @property
    def score(self) -> float:
        return mutation_score(self.killed, self.total)


def mutation_score(killed: int, total: int) -> float:
    """Fraction of mutants caught by the suite (1.0 = every mutant killed)."""
    return 1.0 if total == 0 else killed / total


def _in_string(line: str, col: int) -> bool:
    """Heuristic: is character ``col`` inside a quoted string? Counts unescaped
    quotes before it — odd count → inside a literal. Good enough to skip the
    common false positives (operators inside string content)."""
    seg = line[:col]
    return (seg.count('"') % 2 == 1) or (seg.count("'") % 2 == 1)


def _occurrences(line: str, token: str, bounded: bool) -> list[int]:
    if bounded:
        return [m.start() for m in re.finditer(r"\b" + re.escape(token) + r"\b", line)]
    out, start = [], 0
    while True:
        i = line.find(token, start)
        if i < 0:
            return out
        out.append(i)
        start = i + len(token)


def generate_mutants(source: str, *, max_mutants: int = 50) -> list[Mutant]:
    """Produce up to ``max_mutants`` single-operator mutations of ``source``.
    Skips comment-only lines and matches inside string literals. Pure — no I/O."""
    mutants: list[Mutant] = []
    lines = source.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        for before, after, bounded in _MUTATION_OPS:
            for col in _occurrences(line, before, bounded):
                if _in_string(line, col):
                    continue
                mutated_line = line[:col] + after + line[col + len(before):]
                new_source = "".join(lines[:i] + [mutated_line] + lines[i + 1:])
                mutants.append(Mutant(lineno=i + 1,
                                      label=f"{before.strip()} → {after.strip()}",
                                      mutated_source=new_source))
                if len(mutants) >= max_mutants:
                    return mutants
    return mutants


def _run(test_cmd: str, cwd: Path, timeout: int) -> bool | None:
    """Run the suite; True = passed (exit 0), False = failed, None = timed out."""
    try:
        proc = subprocess.run(test_cmd, shell=True, cwd=cwd,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return proc.returncode == 0


def run_mutants(root: Path | str, target: str, test_cmd: str, *,
                max_mutants: int = 50, timeout: int = 600) -> MutantResult:
    """Mutate ``target`` one operator at a time, run ``test_cmd`` against each,
    and report survivors. Requires a GREEN baseline — if the suite already fails
    on the clean tree, mutation results are meaningless, so we bail. The original
    file is restored in every path (including crash/timeout)."""
    root_path = Path(root).resolve()
    file_path = (root_path / target).resolve()
    if not file_path.is_file():
        return MutantResult(target=target, note=f"{target} is not a file")

    original = file_path.read_text(encoding="utf-8")
    mutants = generate_mutants(original, max_mutants=max_mutants)
    if not mutants:
        return MutantResult(target=target, note="no mutable operators found")

    result = MutantResult(target=target, total=len(mutants))
    try:
        baseline = _run(test_cmd, root_path, timeout)
        if baseline is None:
            return MutantResult(target=target, note=f"baseline run exceeded {timeout}s")
        if baseline is False:
            return MutantResult(target=target,
                                note="baseline suite is already RED — fix tests before mutation testing")
        for m in mutants:
            file_path.write_text(m.mutated_source, encoding="utf-8")
            passed = _run(test_cmd, root_path, timeout)
            # tests still pass on a mutated program → the mutant SURVIVED (bad).
            m.killed = passed is False
            if m.killed:
                result.killed += 1
            else:
                result.survivors.append(m)
    finally:
        file_path.write_text(original, encoding="utf-8")
    return result
