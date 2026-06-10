"""Red-to-green repair loop — turn the pass/fail gate into a feedback signal.

Instead of editing and *hoping*, ronin can run the repo's verify command, and
if it's red, feed the failure straight back to the coding agent — full output
plus the current diff — and let it fix, then re-verify, up to a few rounds,
stopping the moment it goes green. This is what separates a demo from an agent
you can leave to ship: nightshift/kaizen/dojo and the ``/verify`` REPL all build
on it, and because it runs the *detected* command (see :mod:`verify_cmd`) it
works on Node / Go / Rust repos, not just pytest.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .verify_cmd import Verdict, run_verify

# A function that runs the coding agent on one repair instruction. Injected so
# the loop is testable without a live model (the REPL passes a run_code_agent
# closure). Returns nothing meaningful — the loop re-checks via run_verify.
RepairFn = Callable[[str], object]

_REPAIR_PROMPT = """The verify command for this project FAILED after your change.

Command: {command}
{summary}

--- failing output (tail) ---
{output}
--- end output ---

Fix the failure. Inspect the current state (read the relevant files / the diff
below), make the smallest change that makes `{command}` pass, and do NOT weaken
or delete tests to make them pass. When you're done the loop will re-run the
command to check.

--- current diff ---
{diff}
--- end diff ---"""


_DIFF_MAX_LINES = 400          # cap the diff fed to the model (output is capped too)
_TEST_MARKER = re.compile(r"^\s*(def test_|async def test_|func Test|#\[test\]|\bit\(|\btest\()")
_TEST_FILE = re.compile(r"(test_.*\.py|.*_test\.(py|go)|.*\.(test|spec)\.[jt]sx?)$")
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "target", "dist", "build"}


def _git_diff(root: Path) -> str:
    import subprocess
    try:
        proc = subprocess.run(["git", "diff", "HEAD"], cwd=str(root),
                              capture_output=True, text=True, timeout=30)
        out = (proc.stdout or "").strip()
        if not out:
            return "(no tracked changes)"
        lines = out.splitlines()
        if len(lines) > _DIFF_MAX_LINES:
            shown = "\n".join(lines[:_DIFF_MAX_LINES])
            return f"{shown}\n… (diff truncated: {len(lines) - _DIFF_MAX_LINES} more lines)"
        return out
    except (subprocess.SubprocessError, OSError):
        return "(diff unavailable)"


def _count_tests(root: Path) -> int:
    """Count test functions across the repo's test files — a cheap signal for
    detecting an agent that 'fixed' a red suite by deleting the failing test."""
    total = 0
    for p in root.rglob("*"):
        if not p.is_file() or not _TEST_FILE.search(p.name):
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        try:
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                if _TEST_MARKER.match(line):
                    total += 1
        except OSError:
            continue
    return total


def repair_to_green(
    root: Path | str,
    run_agent: RepairFn,
    *,
    rounds: int = 3,
    on_round: Callable[[int, Verdict], None] | None = None,
    timeout: int = 600,
) -> Verdict:
    """Run verify; while it's red, hand the failure to ``run_agent`` and re-verify.

    Returns the final :class:`Verdict`. Stops early on green, when no test
    command exists (``ran=False`` — nothing to repair), or after ``rounds``
    repair attempts. ``on_round(attempt, verdict)`` is called before each repair
    attempt so the caller can narrate progress.
    """
    root = Path(root).resolve()
    verdict = run_verify(root, timeout=timeout)
    # Nothing to do: already green, or no detectable test command (don't invent
    # a "failure" for an untested repo).
    if verdict.passed or not verdict.ran:
        return verdict

    # Snapshot the test inventory so a repair that "goes green" by DELETING the
    # failing test is caught (the gate alone can't tell a fix from a deletion).
    tests_before = _count_tests(root)

    for attempt in range(1, rounds + 1):
        if on_round is not None:
            on_round(attempt, verdict)
        prompt = _REPAIR_PROMPT.format(
            command=verdict.command,
            summary=verdict.summary,
            output=verdict.output or "(no captured output)",
            diff=_git_diff(root),
        )
        run_agent(prompt)
        verdict = run_verify(root, timeout=timeout)
        # Stop on green, OR if a repair broke test detection/runnability (ran=False
        # — keep hammering an undetectable suite is pointless and wastes rounds).
        if verdict.passed or not verdict.ran:
            break

    # Reject a "green" that was bought by removing tests — report it as still-red
    # so callers (and the autonomous --fix path) don't ship a gutted suite.
    if verdict.passed and _count_tests(root) < tests_before:
        return Verdict(
            passed=False, ran=True, command=verdict.command, runner=verdict.runner,
            summary=(f"refused: suite went green but test count dropped "
                     f"({tests_before} → {_count_tests(root)}) — a test was removed, not fixed"),
            output=verdict.output)
    return verdict
