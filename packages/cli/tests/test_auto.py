"""Tests for supervised autonomy (`ronin auto`).

Fully offline: a fake agent (no model, no network) drives the loop, and the
verify gate / checkpoint / signature callables are injected. We assert the three
rails actually fire - checkpoint each step, stop on repeated test failure, and
stop on stall.
"""
from __future__ import annotations

from pathlib import Path

from ronin_cli import auto
from ronin_cli.auto import AutoReport, run_auto_loop, working_tree_signature
from ronin_cli.verify_cmd import Verdict


def _verdict(passed: bool, ran: bool = True) -> Verdict:
    return Verdict(passed=passed, ran=ran, command="uv run pytest -q", runner="pytest",
                   summary=("ok" if passed else "1 failed"), output="boom")


def _counter():
    """A signature source that changes every call -> the tree always 'moves'."""
    state = {"n": 0}

    def sig(_root):
        state["n"] += 1
        return str(state["n"])
    return sig


# ---- CHECKPOINT each step -------------------------------------------------

def test_checkpoints_each_step(tmp_path: Path) -> None:
    made: list[str] = []

    def fake_checkpoint(_root, label):
        made.append(label)
        return len(made)            # 1, 2, 3, ... mimicking checkpoint ids

    report = run_auto_loop(
        tmp_path, "do it", lambda p: None,
        max_steps=3,
        verify_fn=lambda r: _verdict(True),
        signature_fn=_counter(),
        checkpoint_fn=fake_checkpoint,
    )
    # one checkpoint BEFORE step 1, then one AFTER each of the 3 steps
    assert made[0] == "auto: before step 1"
    assert len([m for m in made if m.startswith("auto: after step")]) == 3
    # every step recorded a revertible checkpoint id
    assert [s.checkpoint_id for s in report.steps] == [2, 3, 4]
    assert report.is_git is True


def test_revert_hint_points_at_the_pre_run_checkpoint(tmp_path: Path) -> None:
    ids = {"n": 0}

    def fake_checkpoint(_root, label):
        ids["n"] += 1
        return ids["n"]

    report = run_auto_loop(
        tmp_path, "do it", lambda p: None,
        max_steps=2,
        verify_fn=lambda r: _verdict(True),
        signature_fn=_counter(),
        checkpoint_fn=fake_checkpoint,
    )
    hint = report.revert_hint()
    # first per-step checkpoint is #2, so undoing the whole run rewinds to #1
    assert "ronin rewind 1" in hint
    assert "per-step checkpoints: 2, 3" in hint


# ---- TEST-GATE: stop on repeated failure ----------------------------------

def test_stops_after_consecutive_test_failures(tmp_path: Path) -> None:
    calls: list[str] = []
    report = run_auto_loop(
        tmp_path, "do it", lambda p: calls.append(p),
        max_steps=10,
        max_consecutive_failures=2,
        verify_fn=lambda r: _verdict(False),     # always red
        signature_fn=_counter(),                 # tree keeps changing (not a stall)
        checkpoint_fn=lambda r, lbl: 1,
    )
    # exactly two failing gates in a row, then stop - not all 10 steps
    assert len(report.steps) == 2
    assert len(calls) == 2
    assert report.final_passed is False
    assert "failed 2 step(s) in a row" in report.stopped_reason


def test_failure_streak_resets_on_a_green_gate(tmp_path: Path) -> None:
    # red, green, red, red -> the green resets the streak, so it takes the SECOND
    # pair of reds to trip the 2-in-a-row cap (4 steps, not 2).
    seq = iter([_verdict(False), _verdict(True), _verdict(False), _verdict(False)])
    report = run_auto_loop(
        tmp_path, "do it", lambda p: None,
        max_steps=10,
        max_consecutive_failures=2,
        verify_fn=lambda r: next(seq),
        signature_fn=_counter(),
        checkpoint_fn=lambda r, lbl: 1,
    )
    assert len(report.steps) == 4
    assert "failed 2 step(s) in a row" in report.stopped_reason


# ---- STALL-STOP -----------------------------------------------------------

def test_stops_on_stall(tmp_path: Path) -> None:
    calls: list[str] = []
    report = run_auto_loop(
        tmp_path, "do it", lambda p: calls.append(p),
        max_steps=10,
        stall_after=2,
        verify_fn=lambda r: _verdict(True),
        signature_fn=lambda r: "frozen",        # never changes -> stalled
        checkpoint_fn=lambda r, lbl: 1,
    )
    # two idle steps and then stop; the gate never even runs on an idle step
    assert len(report.steps) == 2
    assert all(not s.changed for s in report.steps)
    assert all(s.verdict is None for s in report.steps)
    assert "stalled" in report.stopped_reason


def test_progress_then_stall(tmp_path: Path) -> None:
    # step 1 changes the tree, steps 2 and 3 do not -> stall trips on step 3.
    sigs = iter(["a", "b", "b", "b"])     # first call = prev_sig, then per step
    report = run_auto_loop(
        tmp_path, "do it", lambda p: None,
        max_steps=10,
        stall_after=2,
        verify_fn=lambda r: _verdict(True),
        signature_fn=lambda r: next(sigs),
        checkpoint_fn=lambda r, lbl: 1,
    )
    assert [s.changed for s in report.steps] == [True, False, False]
    assert "stalled" in report.stopped_reason


# ---- ceiling + no-git -----------------------------------------------------

def test_reaches_step_ceiling_when_all_green(tmp_path: Path) -> None:
    report = run_auto_loop(
        tmp_path, "do it", lambda p: None,
        max_steps=3,
        verify_fn=lambda r: _verdict(True),
        signature_fn=_counter(),
        checkpoint_fn=lambda r, lbl: 1,
    )
    assert len(report.steps) == 3
    assert report.final_passed is True
    assert "step ceiling" in report.stopped_reason


def test_no_git_takes_no_checkpoints(tmp_path: Path) -> None:
    report = run_auto_loop(
        tmp_path, "do it", lambda p: None,
        max_steps=2,
        verify_fn=lambda r: _verdict(True),
        signature_fn=_counter(),
        checkpoint_fn=None,           # no git -> no checkpoints
    )
    report.is_git = False
    assert all(s.checkpoint_id is None for s in report.steps)
    assert "not a git repo" in report.revert_hint()


def test_untested_repo_is_not_counted_as_failure(tmp_path: Path) -> None:
    # ran=False means no test command - it must not trip the failure cap.
    report = run_auto_loop(
        tmp_path, "do it", lambda p: None,
        max_steps=3,
        max_consecutive_failures=2,
        verify_fn=lambda r: _verdict(False, ran=False),
        signature_fn=_counter(),
        checkpoint_fn=lambda r, lbl: 1,
    )
    assert len(report.steps) == 3                 # ran to the ceiling, not stopped
    assert report.final_passed is None
    assert "step ceiling" in report.stopped_reason


# ---- working_tree_signature (real git) ------------------------------------

def test_working_tree_signature_moves_on_edit(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("one\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

    before = working_tree_signature(tmp_path)
    (tmp_path / "f.txt").write_text("two\n")          # tracked edit
    after_edit = working_tree_signature(tmp_path)
    (tmp_path / "new.txt").write_text("new\n")         # untracked add
    after_add = working_tree_signature(tmp_path)

    assert before != after_edit != after_add
    assert before != after_add
