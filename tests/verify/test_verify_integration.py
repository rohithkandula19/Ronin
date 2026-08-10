"""One turn, end to end, with the real objects wired together.

Real detection, a real shadow git repo, a real plan, the real repair loop and the
real critique gate. The only fakes are the two seams that would otherwise reach
outside the process: the command runner (a subprocess) and the model.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from verify_harness import PYTEST_ONE_FAILURE, make_python_project

from ronin.core.types import Mode
from ronin.verify.checkpoints import CheckpointStore, changed_paths_from_diff
from ronin.verify.critique import critique_gate
from ronin.verify.repair import FailureSnapshot, RepairVerdict, repair_loop
from ronin.verify.runner import (
    CommandOutcome,
    as_tool_result,
    plan_for_changes,
    run_command,
    run_plan,
    should_verify,
)
from ronin.verify.spec import detect_verify_spec

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="the integration test uses a real local git"
)


async def _git_repo(root: Path) -> None:
    make_python_project(root)
    await run_command(["git", "init", "--quiet"], cwd=root)
    await run_command(["git", "add", "-A"], cwd=root)
    await run_command(
        ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-qm", "init"],
        cwd=root,
    )


class _Verifier:
    """The real runner over a scripted subprocess, flipped to green by the fixer."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.green = False
        self.spec = detect_verify_spec(root)
        self.tool_results: list[bool] = []

    async def run(self, argv: list[str], *, cwd: Path, timeout: float = 0.0) -> CommandOutcome:
        del cwd, timeout
        if not self.green and any("pytest" in part for part in argv):
            return CommandOutcome(argv=tuple(argv), exit_code=1, output=PYTEST_ONE_FAILURE)
        return CommandOutcome(argv=tuple(argv), exit_code=0, output="")

    async def verify(self, changed: list[str]) -> FailureSnapshot:
        plan = plan_for_changes(self.spec, changed, root=self.root)
        outcome = await run_plan(plan, run=self.run, cwd=self.root)
        self.tool_results.append(as_tool_result(outcome).ok)
        return FailureSnapshot.from_outcome(outcome)


async def test_a_full_turn_checkpoints_verifies_repairs_and_critiques(tmp_path: Path) -> None:
    await _git_repo(tmp_path)
    store = CheckpointStore(tmp_path)
    before_status = await run_command(["git", "status", "--porcelain"], cwd=tmp_path)

    # 1. checkpoint before the mutating turn
    base = await store.checkpoint("before turn 1")
    assert base.ok and base.checkpoint is not None

    # 2. the "model" edits a file
    target = tmp_path / "src" / "pkg" / "thing.py"
    target.write_text("def widget(value: float) -> float:\n    return value\n", encoding="utf-8")

    # 3. the changed paths come from the session diff, not from a guess
    diff = await store.session_diff()
    changed = list(changed_paths_from_diff(diff.diff))
    assert changed == ["src/pkg/thing.py"]
    assert should_verify(mode=Mode.AUTO_EDIT, changed_paths=changed)

    # 4. verify → fails → repair loop, whose fixer actually edits the file
    verifier = _Verifier(tmp_path)

    async def verify() -> FailureSnapshot:
        return await verifier.verify(changed)

    async def fix(snapshot: FailureSnapshot, attempt: int) -> str:
        del attempt
        assert snapshot.failures
        target.write_text(
            "def widget(value: float) -> float:\n    return round(value, 2)\n", encoding="utf-8"
        )
        verifier.green = True
        return "restored the rounding"

    report = await repair_loop(verify=verify, fix=fix)

    assert report.verdict is RepairVerdict.FIXED
    assert len(report.attempts) == 1
    # The first pass was handed back as a *failed tool result*, the second as ok.
    assert verifier.tool_results == [False, True]

    # 5. the critique gate agrees, in one round
    async def ask(prompt: str) -> str:
        assert "rounding" in prompt or "widget" in prompt
        return "yes"

    async def iterate(missing: str) -> str:
        raise AssertionError(f"should not iterate: {missing}")

    final_diff = await store.session_diff()
    gate = await critique_gate(
        objective="keep widget() rounding to two places",
        diff=final_diff.diff,
        ask=ask,
        iterate=iterate,
    )
    assert gate.passes and not gate.iterated

    # 6. /undo still works, and the user's repo never saw any of it
    restored = await store.restore(base.checkpoint.id)
    assert restored.ok
    assert "round(value, 2)" in target.read_text(encoding="utf-8")
    after_status = await run_command(["git", "status", "--porcelain"], cwd=tmp_path)
    assert after_status.output == before_status.output


async def test_an_honest_failure_report_survives_the_whole_wiring(tmp_path: Path) -> None:
    """The other ending: three identical failures, and nobody claims success."""
    await _git_repo(tmp_path)
    store = CheckpointStore(tmp_path)
    assert (await store.checkpoint("before turn 1")).ok
    verifier = _Verifier(tmp_path)

    async def verify() -> FailureSnapshot:
        return await verifier.verify(["src/pkg/thing.py"])

    async def fix(snapshot: FailureSnapshot, attempt: int) -> str:
        del snapshot
        return f"attempt {attempt}: guessed at the rounding again"

    report = await repair_loop(verify=verify, fix=fix)

    assert report.verdict is RepairVerdict.STUCK
    assert report.fixed is False
    rendered = report.render()
    assert rendered.startswith("i could not fix this.")
    assert "tests/pkg/test_thing.py::test_widget_rounds" in rendered
    assert all(ok is False for ok in verifier.tool_results)
