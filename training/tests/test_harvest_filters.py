"""The two gates, the boundary, and the counts. A bad-but-passing run must not teach."""
from __future__ import annotations

import pytest
from ronin_training.harvest.filters import (
    TURN_BUDGET_FACTOR,
    select,
    turn_budgets,
)
from ronin_training.harvest.trajectory import RecordedCall, Step, Trajectory


def _run(run_id: str, turns: int, *, verified: bool = True, task: str = "t1") -> Trajectory:
    return Trajectory(
        task_id=task,
        run_id=run_id,
        prompt="do the thing",
        steps=tuple(
            Step(
                index=i,
                calls=(RecordedCall(id=f"c{i}", name="read_file", arguments={"path": f"f{i}.py"}),),
            )
            for i in range(turns)
        ),
        verified=verified,
    )


def test_a_failing_trajectory_is_dropped_however_short_it_is():
    kept, counts, _ = select([_run("a", 1, verified=False), _run("b", 2)])
    assert [t.run_id for t in kept] == ["b"]
    assert counts.dropped_unverified == 1


def test_the_median_is_taken_over_verified_runs_only():
    """A failing flail must not raise the budget that exists to exclude flailing."""
    corpus = [_run("a", 2), _run("b", 2), _run("flail", 40, verified=False)]
    budgets = turn_budgets(corpus)
    assert budgets["t1"].median_turns == 2.0
    assert budgets["t1"].budget == 3.0
    assert budgets["t1"].sample_size == 2


@pytest.mark.parametrize(
    ("turns", "kept"),
    [
        (2, True),  # at the median
        (3, True),  # exactly 1.5x the median: inclusive, and tested for it
        (4, False),  # over
        (12, False),  # the flail
    ],
)
def test_the_turn_budget_boundary_is_inclusive(turns, kept):
    # Two 2-turn runs fix the median at 2, so the budget is exactly 3.0.
    corpus = [_run("a", 2), _run("b", 2), _run("candidate", turns)]
    survivors, _counts, budgets = select(corpus)
    assert budgets["t1"].budget == 3.0
    assert ("candidate" in [t.run_id for t in survivors]) is kept


def test_the_budget_is_per_task_not_global():
    corpus = [_run("a", 2, task="short"), _run("b", 2, task="short"), _run("c", 10, task="long")]
    budgets = turn_budgets(corpus)
    assert budgets["short"].budget == 3.0
    assert budgets["long"].budget == 15.0
    kept, _counts, _ = select(corpus)
    assert sorted(t.run_id for t in kept) == ["a", "b", "c"]


def test_a_single_verified_run_is_its_own_median_and_survives():
    kept, counts, budgets = select([_run("only", 7)])
    assert [t.run_id for t in kept] == ["only"]
    assert budgets["t1"].median_turns == 7.0
    assert counts.kept == 1


def test_a_verified_run_with_no_turns_is_dropped_and_counted_separately():
    empty = Trajectory(task_id="t1", run_id="empty", prompt="p", steps=(), verified=True)
    kept, counts, _ = select([empty, _run("b", 2)])
    assert [t.run_id for t in kept] == ["b"]
    assert counts.dropped_no_steps == 1
    assert counts.dropped_unverified == 0


def test_every_drop_is_counted_and_the_totals_reconcile():
    corpus = [
        _run("keep1", 2),
        _run("keep2", 2),
        _run("over", 9),
        _run("failed", 2, verified=False),
        Trajectory(task_id="t1", run_id="empty", prompt="p", verified=True),
    ]
    _kept, counts, _ = select(corpus)
    assert counts.as_dict() == {
        "considered": 5,
        "dropped_unverified": 1,
        "dropped_no_steps": 1,
        "dropped_over_turn_budget": 1,
        "kept": 2,
    }
    assert (
        counts.kept
        + counts.dropped_unverified
        + counts.dropped_no_steps
        + counts.dropped_over_turn_budget
        == counts.considered
    )


def test_the_factor_is_configurable_and_the_default_is_the_spec_value():
    assert TURN_BUDGET_FACTOR == 1.5
    corpus = [_run("a", 2), _run("b", 2), _run("wide", 4)]
    tight, _c1, _b1 = select(corpus, factor=1.5)
    loose, _c2, _b2 = select(corpus, factor=2.0)
    assert "wide" not in [t.run_id for t in tight]
    assert "wide" in [t.run_id for t in loose]


def test_the_budget_is_an_exact_float_so_the_boundary_has_no_rounding_slop():
    """Turn counts are integers, so a median is whole or half and 1.5x of either is a
    multiple of 0.25 — exactly representable in binary floating point. The boundary
    comparison in TaskBudget.allows therefore can never be off by an epsilon."""
    for counts in ([1, 2], [3, 4], [5, 5], [7, 8, 9]):
        runs = [_run(f"r{i}", n) for i, n in enumerate(counts)]
        budget = turn_budgets(runs)["t1"].budget
        assert budget * 4 == int(budget * 4)
