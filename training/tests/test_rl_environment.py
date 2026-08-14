"""One rollout, end to end, offline — with the hidden-test rule proven on real files.

No model and no container: the policy is scripted and the sandbox is a subprocess ``bash``,
which is the same ``verify.sh`` a container would run. The task is written to ``tmp_path``
in the eval suite's shape, so this exercises the real reset → run → verify → hidden-verify →
reward path.
"""

from __future__ import annotations

from pathlib import Path

from ronin_training.rl.environment import (
    Environment,
    HiddenTest,
    PolicyResult,
    solution_policy,
)

_VERIFY = """\
#!/usr/bin/env bash
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
"$PY" - <<'PY'
from mod import is_even
assert is_even(2) is True
assert is_even(3) is False
PY
"""

# The held-out suite: broader than the visible one (zero, negatives), so a fix that only
# satisfies the visible cases does not pass here.
_HIDDEN = HiddenTest(
    filename="hidden_check.py",
    body="""\
#!/usr/bin/env bash
set -uo pipefail
PY="${RONIN_EVAL_PYTHON:-python3}"
"$PY" - <<'PY'
from mod import is_even
assert is_even(0) is True
assert is_even(-4) is True
assert is_even(7) is False
PY
""",
)


def _write_task(root: Path) -> Path:
    task = root / "bug-from-trace" / "is-even"
    (task / "fixture").mkdir(parents=True)
    (task / "solution").mkdir(parents=True)
    (task / "fixture" / "mod.py").write_text("def is_even(n):\n    raise NotImplementedError\n")
    (task / "solution" / "mod.py").write_text("def is_even(n):\n    return n % 2 == 0\n")
    (task / "verify.sh").write_text(_VERIFY)
    return task


def test_the_reference_solution_earns_a_passing_reward(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "pool")
    env = Environment()
    rollout = env.rollout(
        task,
        solution_policy(task, turns=3),
        hidden_test=_HIDDEN,
        median_turns=6.0,
        workspace=tmp_path / "ws",
    )
    assert rollout.outcome.verify_passed_visible is True
    assert rollout.outcome.verify_passed_hidden is True
    assert rollout.reward.gate == ""
    assert rollout.reward.base == 1.0
    assert rollout.reward.reward > 1.0  # base + under-median + tool-validity shaping


def test_a_visible_only_overfit_is_not_rewarded_the_hidden_rule_bites(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "pool")

    def overfit(workspace: Path) -> PolicyResult:
        # Passes the visible cases (is_even(2), is_even(3)) and nothing more.
        (workspace / "mod.py").write_text("def is_even(n):\n    return n == 2\n")
        return PolicyResult(turns=2, stated_done=True)

    rollout = Environment().rollout(
        task, overfit, hidden_test=_HIDDEN, median_turns=6.0, workspace=tmp_path / "ws"
    )
    assert rollout.outcome.verify_passed_visible is True, "it did satisfy the visible suite"
    assert rollout.outcome.verify_passed_hidden is False, "but not the held-out one"
    assert rollout.reward.base == 0.0, "the reward keys on hidden, so no task reward"
    assert rollout.outcome.stated_done_while_failing is True
    assert rollout.reward.reward < 0.5


def test_editing_the_tests_is_gated_by_the_rollout(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "pool")

    def cheat(workspace: Path) -> PolicyResult:
        (workspace / "mod.py").write_text("def is_even(n):\n    return n % 2 == 0\n")
        (workspace / "test_evil.py").write_text("def test_pass(): assert True\n")  # the tell
        return PolicyResult(turns=2, stated_done=True)

    rollout = Environment().rollout(
        task, cheat, hidden_test=_HIDDEN, median_turns=6.0, workspace=tmp_path / "ws"
    )
    assert "test_evil.py" in rollout.outcome.diff_paths
    assert rollout.reward.gate.startswith("touched-protected")
    assert rollout.reward.reward == -1.0


def test_a_rollout_that_runs_out_of_turns_is_gated(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "pool")

    def flailer(workspace: Path) -> PolicyResult:
        del workspace  # changed nothing
        return PolicyResult(turns=24, hit_turn_cap=True)

    rollout = Environment(max_turns=24).rollout(
        task, flailer, hidden_test=_HIDDEN, median_turns=6.0, workspace=tmp_path / "ws"
    )
    assert rollout.outcome.ran_out_of_turns is True
    assert rollout.reward.reward == -0.5


def test_the_workspace_is_reset_clean_each_rollout(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "pool")
    ws = tmp_path / "ws"
    # A stale file from a previous run must not survive the reset.
    ws.mkdir()
    (ws / "leftover.txt").write_text("stale")
    Environment().rollout(
        task, solution_policy(task), hidden_test=_HIDDEN, median_turns=6.0, workspace=ws
    )
    assert not (ws / "leftover.txt").exists(), "reset must wipe the workspace"


def test_the_hidden_test_is_never_left_in_the_diff(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "pool")
    rollout = Environment().rollout(
        task,
        solution_policy(task),
        hidden_test=_HIDDEN,
        median_turns=6.0,
        workspace=tmp_path / "ws",
    )
    assert _HIDDEN.filename not in rollout.outcome.diff_paths
    assert rollout.outcome.diff_paths == ("mod.py",)
