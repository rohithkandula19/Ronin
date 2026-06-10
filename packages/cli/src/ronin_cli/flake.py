"""Flaky-test hunter — `ronin flake`.

Runs your test command several times and tells you which tests are
*non-deterministic* — green on one run, red on the next. A single run can't
tell a flaky test from a stable one; ronin runs it N times, diffs the failure
sets, and ranks the offenders. The classification and pytest-output parsing are
pure and unit-tested; the runs themselves are sandboxed subprocess calls.
"""
from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# pytest prints `FAILED path::test - reason` (short summary) and `path::test FAILED`
# (verbose). Capture the node id either way.
import re

_FAILED_SUMMARY_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
_FAILED_VERBOSE_RE = re.compile(r"^(\S+::\S+)\s+(?:FAILED|ERROR)\b", re.MULTILINE)


def parse_pytest_failures(output: str) -> set[str]:
    """Extract failing/erroring test node-ids from pytest output (both the short
    `FAILED path::test` summary and the verbose `path::test FAILED` form)."""
    ids: set[str] = set()
    for m in _FAILED_SUMMARY_RE.finditer(output or ""):
        ids.add(m.group(1).split(" ")[0])
    for m in _FAILED_VERBOSE_RE.finditer(output or ""):
        ids.add(m.group(1))
    return ids


def classify(outcomes: list[bool]) -> str:
    """Given per-run pass booleans: 'stable-pass', 'stable-fail', or 'flaky'."""
    if not outcomes:
        return "unknown"
    if all(outcomes):
        return "stable-pass"
    if not any(outcomes):
        return "stable-fail"
    return "flaky"


def flaky_tests(failure_sets: list[set[str]]) -> set[str]:
    """Tests that failed in SOME runs but not ALL — the non-deterministic ones.
    A test failing in every run is a stable failure, not flaky."""
    if not failure_sets:
        return set()
    union: set[str] = set().union(*failure_sets)
    intersection: set[str] = set(failure_sets[0]).intersection(*failure_sets[1:])
    return union - intersection


@dataclass
class FlakeResult:
    runs: int
    outcomes: list[bool] = field(default_factory=list)        # per-run overall pass
    failure_sets: list[set[str]] = field(default_factory=list)
    verdict: str = "unknown"
    flaky: list[tuple[str, int]] = field(default_factory=list)  # (node-id, fail count)
    note: str = ""


def run_flake(root: Path | str, test_cmd: str, *, runs: int = 5,
              timeout: int = 600) -> FlakeResult:
    """Run ``test_cmd`` ``runs`` times in ``root``, recording pass/fail and the
    set of failing test ids each time, then classify flakiness."""
    root_path = Path(root).resolve()
    outcomes: list[bool] = []
    failure_sets: list[set[str]] = []
    for _ in range(max(1, runs)):
        try:
            proc = subprocess.run(test_cmd, shell=True, cwd=root_path,
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return FlakeResult(runs=runs, outcomes=outcomes, failure_sets=failure_sets,
                               verdict="timeout", note=f"a run exceeded {timeout}s")
        outcomes.append(proc.returncode == 0)
        failure_sets.append(parse_pytest_failures(proc.stdout + "\n" + proc.stderr))

    flaky_ids = flaky_tests(failure_sets)
    counts = Counter()
    for fs in failure_sets:
        for t in fs:
            if t in flaky_ids:
                counts[t] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return FlakeResult(
        runs=len(outcomes),
        outcomes=outcomes,
        failure_sets=failure_sets,
        verdict=classify(outcomes),
        flaky=ranked,
    )
