"""Tests for the local SWE-bench-style harness.

The whole point of the harness is *objective* grading, so these tests use NO
model and NO network: ``grade`` is fed hand-written correct/wrong patches and we
assert it runs the bundled test in a subprocess and reports pass/fail by exit
code. ``score_board`` is a pure renderer, so it's checked directly.
"""
from __future__ import annotations

from ronin_cli import swebench
from ronin_cli.swebench import SweRow, apply_patch, grade, score_board


def _task(task_id: str) -> dict:
    return next(t for t in swebench.TASKS if t["id"] == task_id)


# --------------------------------------------------------------------------- #
# grade — objective pass/fail by running the test in a subprocess (no model)
# --------------------------------------------------------------------------- #

def test_grade_passes_on_known_correct_patch() -> None:
    task = _task("off_by_one_sum")
    fixed = (
        "def sum_to(n):\n"
        "    total = 0\n"
        "    for i in range(1, n + 1):\n"
        "        total += i\n"
        "    return total\n"
    )
    assert grade(task, fixed) is True


def test_grade_fails_on_known_wrong_patch() -> None:
    task = _task("off_by_one_sum")
    # The original buggy code (range(1, n)) still fails sum_to(5) == 15.
    assert grade(task, task["buggy_code"]) is False
    # A patch that returns a constant is also objectively wrong.
    assert grade(task, "def sum_to(n):\n    return 0\n") is False


def test_grade_fails_when_code_does_not_import() -> None:
    # A syntactically broken patch can't even be imported → must be graded FAIL,
    # never raise.
    task = _task("off_by_one_sum")
    assert grade(task, "def sum_to(n)\n    return") is False


def test_grade_handles_a_second_task() -> None:
    # Sanity-check a different bundled issue so the battery isn't one-off.
    task = _task("empty_average_crash")
    good = (
        "def average(xs):\n"
        "    return sum(xs) / len(xs) if xs else 0.0\n"
    )
    assert grade(task, good) is True
    assert grade(task, task["buggy_code"]) is False  # crashes on []


def test_apply_patch_takes_patched_else_original() -> None:
    assert apply_patch("old", "new") == "new"
    assert apply_patch("old", "   ") == "old"  # empty patch → keep original


def test_tasks_are_well_formed() -> None:
    assert len(swebench.TASKS) >= 6
    for t in swebench.TASKS:
        assert {"id", "summary", "module", "buggy_code", "test"} <= t.keys()
        # The bundled buggy code must actually FAIL its own test (so a no-op
        # "fix" can't score) ...
        assert grade(t, t["buggy_code"]) is False, f"{t['id']} buggy code should fail"


# --------------------------------------------------------------------------- #
# score_board — pure leaderboard renderer (model · rate · avg-cost · avg-time)
# --------------------------------------------------------------------------- #

def test_score_board_sorts_by_rate_then_cost() -> None:
    rows = [
        SweRow("anthropic:claude", "anthropic", passed=6, total=6, tokens=1_000_000, elapsed=12.0),
        SweRow("gemini:flash", "gemini", passed=6, total=6, tokens=1_000_000, elapsed=18.0),
        SweRow("ollama:llama3.1", "ollama", passed=2, total=6, tokens=500_000, elapsed=30.0),
    ]
    board = score_board(rows)
    lines = board.splitlines()[2:]  # drop header + rule
    order = [ln.split()[1] for ln in lines]
    # Both perfect scorers rank above the 2/6 model; among the perfect pair the
    # FREE one (gemini, avg-cost 0) beats the paid one (anthropic).
    assert order == ["gemini:flash", "anthropic:claude", "ollama:llama3.1"]
    assert "free" in board  # gemini renders as free
    assert "100%" in board


def test_score_board_renders_errored_row() -> None:
    rows = [
        SweRow("anthropic:claude", "anthropic", passed=4, total=6, tokens=10_000, elapsed=8.0),
        SweRow("dead:model", "dead", passed=0, total=6, error="connection refused"),
    ]
    board = score_board(rows)
    assert "err" in board
    # the healthy model is ranked first (errored row sinks to the bottom)
    assert board.splitlines()[2].split()[1] == "anthropic:claude"


def test_swerow_cost_helpers() -> None:
    free = SweRow("gemini:x", "gemini", 6, 6, 1_000_000, 6.0)
    assert free.est_cost == 0.0 and free.avg_cost == 0.0
    paid = SweRow("anthropic:x", "anthropic", 6, 6, 1_000_000, 6.0)
    assert paid.est_cost == swebench.COST_PER_MTOK["anthropic"]
    assert paid.avg_cost == swebench.COST_PER_MTOK["anthropic"] / 6
    unknown = SweRow("custom:x", "custom", 6, 6, 1000, 6.0)
    assert unknown.est_cost is None and unknown.avg_cost is None
    assert free.rate == 1.0 and free.avg_time == 1.0
