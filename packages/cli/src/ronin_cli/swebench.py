"""``ronin swebench`` — a LOCAL, Docker-free, SWE-bench-STYLE coding leaderboard.

SWE-bench measures something ``ronin eval`` / ``ronin bench`` don't quite: can a
model take a *bug report + a failing test* and produce a patch that makes the
test pass? The grade is the test runner's exit code — **objective outcome, never
an LLM judge**, exactly like the rest of ronin's eval surface. So we can run the
identical issue battery across Claude / Gemini / Cerebras / ollama and rank them
on the one thing that matters: did the fix actually work?

WHAT THIS IS (and isn't)
------------------------
This is a *local proxy* for SWE-bench, not the real benchmark. Real SWE-bench
pulls thousands of GitHub issues from large OSS repos and grades each candidate
patch inside a per-issue **Docker** image with the project's full dependency
tree. That needs Docker + the dataset download + minutes-to-hours per run.

Instead, this module ships a small, **self-contained, bundled** ``TASKS`` list:
each "issue" is a tiny buggy Python snippet (a string) plus a failing
pytest-style test (a string), solvable by a small edit. Grading writes the
model's code + the task's test to a throwaway temp dir and runs the test in a
subprocess — Docker-free, deterministic, offline. It captures the *shape* of
SWE-bench (issue → patch → test passes/fails) so you get a real, reproducible
coding leaderboard in seconds, on any provider, with zero infra.

CLI (paste-in — this module deliberately does NOT edit main.py)
---------------------------------------------------------------
Add to ``packages/cli/src/ronin_cli/main.py`` to expose ``ronin swebench``::

    @app.command()
    def swebench(
        models: str = typer.Option(
            ..., "--models", "-m",
            help="Comma-separated provider[:model] specs, e.g. "
                 "'anthropic,gemini,cerebras,ollama:llama3.1'."),
    ) -> None:
        '''🐛 SWE-bench-style coding leaderboard — each model fixes a battery of
        bugs (issue + failing test); the fix is graded by RUNNING the test, not by
        an LLM judge. A local, Docker-free proxy for SWE-bench.'''
        from .swebench import run as run_swebench
        run_swebench([s.strip() for s in models.split(",") if s.strip()], console=console)

Pure, testable core (no model / no network needed to unit-test):
``apply_patch`` · ``grade`` · ``score_board``.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_eval import _EVAL_SYSTEM
from .bench import COST_PER_MTOK, FREE_PROVIDERS
from .code_mode import run_code_agent
from .config import RoninConfig, load_config
from .runner import config_for_spec


# --------------------------------------------------------------------------- #
# The bundled "issues". Each is a small, self-contained bug solvable by a tiny
# edit. ``module`` is the file the snippet lives in; the ``test`` imports from it
# (``from solution import ...``) and asserts the FIXED behaviour, so a correct
# patch turns the test green and a wrong/no-op patch leaves it red — objectively.
# --------------------------------------------------------------------------- #

MODULE_NAME = "solution"  # the file the model's code is written to (test imports it)

TASKS: list[dict[str, str]] = [
    {
        "id": "off_by_one_sum",
        "summary": (
            "sum_to(n) should return the sum of integers 1..n inclusive, but it "
            "stops at n-1 (off-by-one in the range). Fix it so sum_to(5) == 15."
        ),
        "module": f"{MODULE_NAME}.py",
        "buggy_code": (
            "def sum_to(n):\n"
            "    total = 0\n"
            "    for i in range(1, n):  # bug: excludes n\n"
            "        total += i\n"
            "    return total\n"
        ),
        "test": (
            "from solution import sum_to\n"
            "\n"
            "def test_sum_to():\n"
            "    assert sum_to(5) == 15\n"
            "    assert sum_to(1) == 1\n"
            "    assert sum_to(0) == 0\n"
        ),
    },
    {
        "id": "fizzbuzz_order",
        "summary": (
            "fizzbuzz(n) returns 'Fizz' for multiples of 3, 'Buzz' for 5, "
            "'FizzBuzz' for 15, else the number as a string. The checks are "
            "ordered wrong so 15 yields 'Fizz' instead of 'FizzBuzz'. Fix it."
        ),
        "module": f"{MODULE_NAME}.py",
        "buggy_code": (
            "def fizzbuzz(n):\n"
            "    if n % 3 == 0:\n"
            "        return 'Fizz'\n"
            "    if n % 5 == 0:\n"
            "        return 'Buzz'\n"
            "    if n % 15 == 0:  # unreachable: 3 and 5 already matched\n"
            "        return 'FizzBuzz'\n"
            "    return str(n)\n"
        ),
        "test": (
            "from solution import fizzbuzz\n"
            "\n"
            "def test_fizzbuzz():\n"
            "    assert fizzbuzz(15) == 'FizzBuzz'\n"
            "    assert fizzbuzz(3) == 'Fizz'\n"
            "    assert fizzbuzz(5) == 'Buzz'\n"
            "    assert fizzbuzz(7) == '7'\n"
        ),
    },
    {
        "id": "empty_average_crash",
        "summary": (
            "average(xs) divides by len(xs) and raises ZeroDivisionError on an "
            "empty list. It should return 0.0 for an empty list instead."
        ),
        "module": f"{MODULE_NAME}.py",
        "buggy_code": (
            "def average(xs):\n"
            "    return sum(xs) / len(xs)  # bug: crashes when xs is empty\n"
        ),
        "test": (
            "from solution import average\n"
            "\n"
            "def test_average():\n"
            "    assert average([2, 4, 6]) == 4.0\n"
            "    assert average([]) == 0.0\n"
        ),
    },
    {
        "id": "mutable_default_arg",
        "summary": (
            "append_item(item, bucket=[]) uses a mutable default argument, so the "
            "list is shared across calls and accumulates. Each call with no bucket "
            "should start from an empty list."
        ),
        "module": f"{MODULE_NAME}.py",
        "buggy_code": (
            "def append_item(item, bucket=[]):  # bug: shared mutable default\n"
            "    bucket.append(item)\n"
            "    return bucket\n"
        ),
        "test": (
            "from solution import append_item\n"
            "\n"
            "def test_append_item():\n"
            "    assert append_item(1) == [1]\n"
            "    assert append_item(2) == [2]  # must NOT be [1, 2]\n"
            "    assert append_item('x', ['a']) == ['a', 'x']\n"
        ),
    },
    {
        "id": "reverse_words_inplace",
        "summary": (
            "reverse_words(s) should reverse the ORDER of words in a sentence "
            "('hello world' -> 'world hello'), but it reverses the characters "
            "instead. Fix it to reverse word order, preserving single spaces."
        ),
        "module": f"{MODULE_NAME}.py",
        "buggy_code": (
            "def reverse_words(s):\n"
            "    return s[::-1]  # bug: reverses characters, not words\n"
        ),
        "test": (
            "from solution import reverse_words\n"
            "\n"
            "def test_reverse_words():\n"
            "    assert reverse_words('hello world') == 'world hello'\n"
            "    assert reverse_words('a b c') == 'c b a'\n"
            "    assert reverse_words('single') == 'single'\n"
        ),
    },
    {
        "id": "is_palindrome_case",
        "summary": (
            "is_palindrome(s) should ignore case and non-alphanumeric characters "
            "('A man, a plan, a canal: Panama' is a palindrome) but it compares "
            "the raw string, so it fails on mixed case / punctuation. Fix it."
        ),
        "module": f"{MODULE_NAME}.py",
        "buggy_code": (
            "def is_palindrome(s):\n"
            "    return s == s[::-1]  # bug: case- and punctuation-sensitive\n"
        ),
        "test": (
            "from solution import is_palindrome\n"
            "\n"
            "def test_is_palindrome():\n"
            "    assert is_palindrome('A man, a plan, a canal: Panama') is True\n"
            "    assert is_palindrome('RaceCar') is True\n"
            "    assert is_palindrome('hello') is False\n"
        ),
    },
]


# A focused harness prompt for the SWE-bench coding path: hand the model the
# buggy file + the failing test and have it WRITE the fixed file. Mirrors the
# objective, tool-acting style of ``agent_eval`` so it's fair across providers.
_SWE_SYSTEM = _EVAL_SYSTEM + (
    "\n\nYou are fixing a bug. You are given a buggy source file and a failing "
    "test. Edit the source file with write_file so the test passes. Keep the "
    "public function names and signatures the test imports unchanged. Do NOT "
    "edit the test file. When done, give a one-line confirmation."
)


def _task_prompt(task: dict[str, str]) -> str:
    """The instruction handed to the coding agent for one issue."""
    return (
        f"Bug report:\n{task['summary']}\n\n"
        f"The file `{task['module']}` currently contains:\n\n"
        f"```python\n{task['buggy_code']}```\n\n"
        f"It must satisfy this test (do not edit the test):\n\n"
        f"```python\n{task['test']}```\n\n"
        f"Fix `{task['module']}` so the test passes. Use write_file to save the "
        f"corrected file; keep the same function name(s) the test imports."
    )


# --------------------------------------------------------------------------- #
# Pure, testable core
# --------------------------------------------------------------------------- #

def apply_patch(original: str, patched: str) -> str:
    """Apply a model's edit. In this local harness the model rewrites the whole
    file, so a 'patch' is just the new file contents — applying it means taking
    the patched version. Kept as a named seam so a future unified-diff patcher
    can slot in without touching callers. An empty patch leaves the original."""
    return patched if patched.strip() else original


def grade(task: dict[str, str], model_patched_code: str) -> bool:
    """OBJECTIVELY grade a candidate fix: write the model's code as the task's
    module + the task's test into a throwaway temp dir, then run the test in a
    subprocess. Returns True iff the test process exits 0.

    No model, no network — pure I/O + a subprocess — so it's unit-testable with a
    hand-written correct/wrong patch. Prefers ``pytest`` (every task's test is a
    ``test_*`` function); falls back to running the test file directly with a
    tiny shim if pytest isn't importable, so the grade never depends on infra."""
    code = apply_patch(task["buggy_code"], model_patched_code)
    sandbox = Path(tempfile.mkdtemp(prefix="ronin-swebench-"))
    try:
        (sandbox / task["module"]).write_text(code, encoding="utf-8")
        test_file = sandbox / f"test_{task['id']}.py"
        test_file.write_text(task["test"], encoding="utf-8")
        return _run_test(sandbox, test_file)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def _run_test(sandbox: Path, test_file: Path) -> bool:
    """Run one test file in ``sandbox`` and report pass/fail by exit code.

    Uses ``python -m pytest -q`` when pytest is available (the common case);
    otherwise a minimal fallback that imports the file and calls every ``test_*``
    function so a missing pytest can't make grading impossible. Bounded by a
    timeout and never raises — any error (timeout, import failure) is a FAIL."""
    have_pytest = _pytest_available()
    if have_pytest:
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file.name)]
    else:
        cmd = [sys.executable, "-c", _FALLBACK_RUNNER, test_file.name]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(sandbox),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001 — any failure to even run the test is a FAIL
        return False


def _pytest_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("pytest") is not None


# Minimal stand-in for `pytest` when it isn't installed: import the test module
# (its directory is on sys.path because we run with cwd=sandbox) and call every
# zero-arg `test_*` function; exit non-zero on the first assertion that fails.
_FALLBACK_RUNNER = """
import importlib, sys
name = sys.argv[1][:-3] if sys.argv[1].endswith('.py') else sys.argv[1]
sys.path.insert(0, '.')
mod = importlib.import_module(name)
fns = [getattr(mod, n) for n in dir(mod) if n.startswith('test_') and callable(getattr(mod, n))]
failed = 0
for fn in fns:
    try:
        fn()
    except Exception as e:
        failed += 1
        print(f'FAIL {fn.__name__}: {e}')
sys.exit(1 if (failed or not fns) else 0)
"""


@dataclass
class SweRow:
    """One model's standing on the issue battery."""
    label: str
    provider: str
    passed: int
    total: int
    tokens: int = 0
    elapsed: float = 0.0
    error: str | None = None

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def est_cost(self) -> float | None:
        """Estimated USD for this run. Free providers → 0.0; unknown → None.
        Reuses ``ronin bench``'s cost table so the two leaderboards agree."""
        if self.provider in FREE_PROVIDERS:
            return 0.0
        per_mtok = COST_PER_MTOK.get(self.provider)
        return None if per_mtok is None else self.tokens / 1_000_000 * per_mtok

    @property
    def avg_cost(self) -> float | None:
        c = self.est_cost
        return None if c is None else (c / self.total if self.total else 0.0)

    @property
    def avg_time(self) -> float:
        return self.elapsed / self.total if self.total else 0.0


def _rank_key(row: SweRow) -> tuple:
    """Leaderboard order: most fixes first, then cheapest, then fastest. Unknown
    cost sorts after any known cost. (Errored rows have rate 0 → sink.)"""
    cost = row.avg_cost
    cost_rank = float("inf") if cost is None else cost
    return (-row.rate, cost_rank, row.avg_time)


def score_board(rows: list[SweRow]) -> str:
    """Render a plain-text leaderboard: ``model · pass-rate · avg-cost · avg-time``,
    sorted by pass-rate (desc) then avg-cost (asc). Pure — no Rich, no console —
    so it's trivially unit-testable and also fine to log/pipe."""
    header = f"{'#':<3} {'model':<26} {'pass':>7} {'rate':>6} {'avg $':>9} {'avg t':>8}"
    lines = [header, "-" * len(header)]
    for i, r in enumerate(sorted(rows, key=_rank_key), 1):
        if r.error:
            lines.append(f"{i:<3} {r.label:<26} {'err':>7} {'—':>6} {'—':>9} {'—':>8}")
            continue
        cost = r.avg_cost
        cost_s = "free" if cost == 0 else (f"${cost:.5f}" if cost is not None else "—")
        lines.append(
            f"{i:<3} {r.label:<26} {f'{r.passed}/{r.total}':>7} "
            f"{r.rate * 100:5.0f}% {cost_s:>9} {r.avg_time:6.1f}s"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Model-driven run (uses ronin's provider layer ONLY — no anthropic/openai import)
# --------------------------------------------------------------------------- #

def _solve_one(cfg: RoninConfig, task: dict[str, str], *, max_iterations: int) -> tuple[str, int, float]:
    """Drive the coding path for ONE issue: write the buggy file into a sandbox,
    ask the configured blade to fix it, then read the (possibly) edited file back.
    Returns ``(patched_code, tokens, elapsed)``. The returned code is graded
    separately by ``grade`` so scoring stays objective and decoupled."""
    sandbox = Path(tempfile.mkdtemp(prefix="ronin-swebench-solve-"))
    module_path = sandbox / task["module"]
    try:
        module_path.write_text(task["buggy_code"], encoding="utf-8")
        # Seed the failing test in the sandbox too, so an agent that runs the
        # tests sees the real red bar (it must not edit it — the system prompt says so).
        (sandbox / f"test_{task['id']}.py").write_text(task["test"], encoding="utf-8")
        t0 = time.time()
        res = run_code_agent(
            cfg,
            _task_prompt(task),
            root=sandbox,
            console=None,
            yolo=True,
            max_iterations=max_iterations,
            base_system=_SWE_SYSTEM,
            include_image_tool=False,
        )
        elapsed = time.time() - t0
        patched = module_path.read_text(encoding="utf-8", errors="ignore") if module_path.is_file() else ""
        usage = res.usage or {}
        tokens = int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
        return patched, tokens, elapsed
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def run_model(base: RoninConfig, spec: dict[str, Any], *, max_iterations: int = 20) -> SweRow:
    """Run every bundled issue through one model and grade each fix objectively."""
    cfg = config_for_spec(base, spec)
    label = f"{cfg.provider}:{cfg.resolved_model()}"
    passed = tokens = 0
    elapsed = 0.0
    for task in TASKS:
        try:
            patched, t_tokens, t_elapsed = _solve_one(cfg, task, max_iterations=max_iterations)
        except Exception as e:  # noqa: BLE001 — a dead model shouldn't sink the board
            return SweRow(label, cfg.provider, passed, len(TASKS), tokens, elapsed, error=str(e))
        tokens += t_tokens
        elapsed += t_elapsed
        if grade(task, patched):
            passed += 1
    return SweRow(label, cfg.provider, passed, len(TASKS), tokens, elapsed)


def run(models: list[str], *, console, max_iterations: int = 20) -> None:
    """For each model spec, fix every bundled issue, grade by RUNNING the test,
    then print the leaderboard. ``models`` is a list of ``provider[:model]``
    strings. Uses ronin's provider layer only."""
    from .consensus import parse_model_spec
    from .theme import ACCENT, MUTE, OK

    specs = [parse_model_spec(s) for s in models if s and s.strip()]
    if not specs:
        console.print("[yellow]give at least one model[/yellow] — e.g. "
                      "[cyan]-m anthropic,gemini[/cyan]")
        return

    base = load_config()
    labels = ", ".join(
        f"{s['provider']}{':' + s['model'] if s.get('model') else ''}" for s in specs
    )
    console.print(
        f"[dim]🐛 SWE-bench-style — {len(specs)} model(s) fixing {len(TASKS)} "
        f"bundled issues, graded by running the tests — {labels}\n"
        f"(local Docker-free proxy for SWE-bench; several real model calls each)…[/dim]"
    )

    rows: list[SweRow] = []
    for spec in specs:
        spec_label = f"{spec['provider']}{':' + spec['model'] if spec.get('model') else ''}"
        with console.status(f"[dim] {spec_label} fixing issues…[/dim]", spinner="dots"):
            rows.append(run_model(base, spec, max_iterations=max_iterations))

    console.print()
    console.print(f"[bold]ronin swebench[/bold]  [dim]· objective coding leaderboard "
                  f"({len(TASKS)} issues · pass = test runner exit 0)[/dim]")
    console.print()
    # The pure board, dimmed-but-monospace so columns line up in any terminal.
    for line in score_board(rows).splitlines():
        console.print(f"  [{MUTE}]{line}[/{MUTE}]" if line.startswith("-") else f"  {line}")
    console.print()

    ranked = sorted(rows, key=_rank_key)
    winners = [r for r in ranked if not r.error and r.total]
    if winners and winners[0].rate > 0:
        best = winners[0]
        cost = best.avg_cost
        price = "free" if cost == 0 else (f"~${cost:.5f}/issue" if cost is not None else "cost unknown")
        console.print(
            f"  [bold {ACCENT}]▶ best:[/bold {ACCENT}] [{OK}]{best.label}[/{OK}] — "
            f"{best.passed}/{best.total} fixed ({best.rate:.0%}), {price}"
        )
    else:
        console.print("  [yellow]no model fixed any issue.[/yellow]")
    console.print()
