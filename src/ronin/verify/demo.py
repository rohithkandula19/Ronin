"""Runnable proof of the verification loop: ``python -m ronin.verify.demo``.

Offline and self-contained. It builds a synthetic project in a temp directory
(including a real, tiny git repo), then walks the five things this package
promises:

1. a :class:`~ronin.verify.spec.VerifySpec` detected from evidence, with the
   provenance of every command and an honest note where there is none;
2. a narrowed plan for one changed file, run against a *scripted* command runner,
   with the failure arriving as a ``ToolResult`` — an observation, not a new
   instruction;
3. a repair loop that gives up honestly once the same failure signature comes
   back for the third time;
4. a checkpoint, a restore and a session diff, with proof that the user's own
   ``git`` index and ``git status`` were untouched;
5. a self-critique that says "no" once, buys exactly one more iteration, and then
   passes.

Nothing here reaches the network, and no model is called: the two model-shaped
seams (the fixer and the critic) are scripted callables.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .checkpoints import CheckpointStore, unavailable_note
from .critique import CritiqueGate, critique_gate
from .repair import FailureSnapshot, RepairReport, repair_loop
from .runner import (
    CommandFailure,
    CommandOutcome,
    as_tool_result,
    plan_for_changes,
    run_command,
    run_plan,
)
from .spec import detect_verify_spec

_PYPROJECT = """\
[project]
name = "widget"
version = "0.1.0"

[dependency-groups]
dev = ["pytest>=8", "mypy>=1.11", "ruff>=0.6"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100

[tool.mypy]
strict = true
"""

#: A realistic failing pytest run. Reused with cosmetic differences below to show
#: that the signature ignores them.
_PYTEST_FAILURE = """\
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-8.3.2
rootdir: /tmp/pytest-of-demo/pytest-17/proj0
collected 3 items

tests/pkg/test_thing.py .F.                                              [100%]

=================================== FAILURES ===================================
_____________________________ test_widget_rounds ______________________________
tests/pkg/test_thing.py:14: in test_widget_rounds
    assert widget(1.005) == 1.01
E   AssertionError: assert 1.0 == 1.01
=========================== short test summary info ============================
FAILED tests/pkg/test_thing.py::test_widget_rounds - AssertionError: assert 1.0 == 1.01
========================= 1 failed, 2 passed in 0.11s ==========================
"""

#: The same failure, one run later: a different tmpdir, a line number that moved
#: because an import was added, a different duration, a different plugin list.
_PYTEST_FAILURE_NOISY = """\
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-8.3.2
rootdir: /tmp/pytest-of-demo/pytest-23/proj0
plugins: anyio-4.4.0
collected 3 items

tests/pkg/test_thing.py .F.                                              [100%]

=================================== FAILURES ===================================
_____________________________ test_widget_rounds ______________________________
tests/pkg/test_thing.py:19: in test_widget_rounds
    assert widget(1.005) == 1.01
E   AssertionError: assert 1.0 == 1.01
=========================== short test summary info ============================
FAILED tests/pkg/test_thing.py::test_widget_rounds - AssertionError: assert 1.0 == 1.01
========================= 1 failed, 2 passed in 0.28s ==========================
"""


def _heading(text: str) -> None:
    print()
    print(f"── {text} " + "─" * max(0, 68 - len(text)))


def _build_project(root: Path) -> None:
    (root / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (root / "uv.lock").write_text("# pretend lockfile\n", encoding="utf-8")
    (root / ".gitignore").write_text(".ronin/\n", encoding="utf-8")
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "thing.py").write_text(
        "def widget(value: float) -> float:\n    return round(value, 2)\n", encoding="utf-8"
    )
    (root / "tests" / "pkg" / "test_thing.py").write_text(
        "from pkg.thing import widget\n\n\ndef test_widget_rounds() -> None:\n"
        "    assert widget(1.005) == 1.01\n",
        encoding="utf-8",
    )
    # A source file with no test anywhere, so the honest "no test found" note is
    # visible rather than hypothetical.
    (root / "src" / "pkg" / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")


async def _scripted_runner(
    argv: Sequence[str], *, cwd: Path, timeout: float = 0.0, env: object = None
) -> CommandOutcome:
    """A command runner with no subprocesses: pytest fails, everything else passes."""
    del cwd, timeout, env
    if any("pytest" in part for part in argv):
        return CommandOutcome(argv=tuple(argv), exit_code=1, output=_PYTEST_FAILURE)
    return CommandOutcome(argv=tuple(argv), exit_code=0, output="")


async def _demo_spec_and_plan(root: Path) -> int:
    _heading("1. detecting what 'verified' means here")
    spec = detect_verify_spec(root)
    print(spec.explain())

    _heading("2. the narrowed plan for one changed file")
    plan = plan_for_changes(spec, ["src/pkg/thing.py", "src/pkg/orphan.py"], root=root)
    print(plan.render())

    outcome = await run_plan(plan, run=_scripted_runner, cwd=root)
    result = as_tool_result(outcome)
    print()
    print(f"ToolResult.ok = {result.ok}   (a failure is an observation, not a user turn)")
    print(result.error.splitlines()[0])
    print("…")
    print(result.error.splitlines()[-1])
    if result.ok:
        print("UNEXPECTED: the scripted pytest failure did not surface")
        return 1
    return 0


async def _demo_repair() -> tuple[int, RepairReport]:
    _heading("3. a repair loop that gives up honestly")

    outputs = [_PYTEST_FAILURE, _PYTEST_FAILURE_NOISY, _PYTEST_FAILURE]
    calls = {"n": 0}

    async def verify() -> FailureSnapshot:
        index = min(calls["n"], len(outputs) - 1)
        calls["n"] += 1
        return FailureSnapshot.from_output(outputs[index], ok=False)

    async def fix(snapshot: FailureSnapshot, attempt: int) -> str:
        del snapshot
        return (
            f"--- a/src/pkg/thing.py (attempt {attempt}: tweaked the rounding)\n"
            "+++ b/src/pkg/thing.py"
        )

    report = await repair_loop(verify=verify, fix=fix)
    print(report.render())
    runs = (report.baseline, *(attempt.snapshot for attempt in report.attempts))
    signatures = {snapshot.signature for snapshot in runs}
    print()
    print(
        f"distinct signatures across all three runs: {len(signatures)} "
        f"(tmpdir, line number and duration differed every time)"
    )
    if len(signatures) != 1 or report.fixed:
        print("UNEXPECTED: the noise was not stripped, or the loop claimed success")
        return 1, report
    return 0, report


async def _demo_checkpoints(root: Path) -> int:
    _heading("4. checkpoint, restore, session diff — without touching the user's index")

    real_index = root / ".git" / "index"
    before_index = real_index.read_bytes() if real_index.is_file() else b""
    before_status = await run_command(["git", "status", "--porcelain"], cwd=root)

    store = CheckpointStore(root)
    availability = await store.ensure()
    if not availability.available:
        print(unavailable_note(availability))
        print("(that is the named, non-fatal degradation — the session continues)")
        return 0

    first = await store.checkpoint("before the edit")
    if not first.ok or first.checkpoint is None:
        print(f"UNEXPECTED: {first.detail}")
        return 1
    print(f"checkpoint {first.checkpoint.short}  {first.checkpoint.label}")

    target = root / "src" / "pkg" / "thing.py"
    original = target.read_bytes()
    target.write_bytes(b"def widget(value: float) -> float:\n    return value\n")
    (root / "src" / "pkg" / "added.py").write_text("EXTRA = True\n", encoding="utf-8")

    diff = await store.session_diff()
    changed = [line for line in diff.diff.splitlines() if line.startswith(("+++", "---"))]
    print(f"session_diff(): {len(diff.diff.splitlines())} lines, touching {changed}")

    restored = await store.restore(first.checkpoint.id)
    print(f"restore({first.checkpoint.short}) → ok={restored.ok}")

    problems: list[str] = []
    if target.read_bytes() != original:
        problems.append("the edited file was not restored byte-for-byte")
    if (root / "src" / "pkg" / "added.py").exists():
        problems.append("the file created after the checkpoint was not removed")

    after_index = real_index.read_bytes() if real_index.is_file() else b""
    after_status = await run_command(["git", "status", "--porcelain"], cwd=root)
    if after_index != before_index:
        problems.append("the user's .git/index changed")
    if after_status.output != before_status.output:
        problems.append(f"git status changed: {before_status.output!r} → {after_status.output!r}")
    print("the user's own repo, before and after:")
    print(f"  .git/index bytes identical: {after_index == before_index}")
    print(f"  git status --porcelain identical: {after_status.output == before_status.output}")
    print(f"  shadow repo lives at: {store.git_dir.relative_to(root)}")

    if problems:
        for problem in problems:
            print(f"UNEXPECTED: {problem}")
        return 1
    return 0


async def _demo_critique() -> tuple[int, CritiqueGate]:
    _heading("5. one self-critique, one extra iteration, then done")

    answers = ["no\nthe --json flag the request asked for is not implemented", "yes"]
    asked: list[str] = []

    async def ask(prompt: str) -> str:
        asked.append(prompt)
        return answers[min(len(asked) - 1, len(answers) - 1)]

    async def iterate(missing: str) -> str:
        print(f"iterating once on: {missing}")
        return "+    parser.add_argument('--json', action='store_true')\n"

    gate = await critique_gate(
        objective="add a --json flag to the report command",
        diff="+def report():\n+    print('text')\n",
        ask=ask,
        iterate=iterate,
    )
    print(gate.render())
    print(f"model calls: {len(asked)} (capped at two rounds, always)")
    if not gate.passes or not gate.iterated:
        print("UNEXPECTED: the gate did not iterate once and then pass")
        return 1, gate
    return 0, gate


async def main() -> int:
    """Run the whole demo in a temp dir. Returns 0 when every claim held."""
    failures = 0
    with tempfile.TemporaryDirectory(prefix="ronin-verify-demo-") as tmp:
        root = Path(tmp).resolve()
        _build_project(root)

        created = await run_command(["git", "init", "--quiet"], cwd=root)
        if created.failure is CommandFailure.NOT_FOUND:
            print(
                "git is not installed; the checkpoint section will show the "
                "degraded path instead of the happy path"
            )
        else:
            for argv in (
                ["git", "add", "-A"],
                [
                    "git",
                    "-c",
                    "user.name=demo",
                    "-c",
                    "user.email=demo@localhost",
                    "commit",
                    "--quiet",
                    "-m",
                    "initial",
                ],
            ):
                await run_command(argv, cwd=root)

        failures += await _demo_spec_and_plan(root)
        repair_status, _report = await _demo_repair()
        failures += repair_status
        failures += await _demo_checkpoints(root)
        critique_status, _gate = await _demo_critique()
        failures += critique_status

    _heading("result")
    if failures:
        print(f"{failures} section(s) did not do what this package claims")
        return 1
    print("every section did what it claims: detection is evidence-based, failures")
    print("arrive as tool results, the repair loop admits defeat, checkpoints leave")
    print("the user's index alone, and the critique gate is capped at one retry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
