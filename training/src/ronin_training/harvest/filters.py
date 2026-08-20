"""Which trajectories are allowed to teach. Two gates, and a count for each drop.

The user's spec, restated as code: a trajectory may become training data only if
``verify.sh`` passed **and** its turn count is within 1.5x the median for that
task. The second gate exists because a bad-but-passing run teaches bad habits — a
model shown a twelve-turn flail that happened to end green learns to flail.

Every drop is counted and reported. A filter whose effect is not measured is a
filter nobody can argue with, and the counts are the only evidence that the corpus
is small because the material is thin rather than because the code is broken.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

from .trajectory import Trajectory

#: "Within 1.5x the median for that task", from the spec. Configurable per call so a
#: task family with legitimately long runs can be widened with the change visible.
TURN_BUDGET_FACTOR = 1.5


@dataclass(frozen=True, slots=True)
class FilterCounts:
    """How many trajectories each gate removed. Real measurement, reported as-is."""

    considered: int = 0
    dropped_unverified: int = 0
    dropped_no_steps: int = 0
    dropped_over_turn_budget: int = 0
    kept: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "considered": self.considered,
            "dropped_unverified": self.dropped_unverified,
            "dropped_no_steps": self.dropped_no_steps,
            "dropped_over_turn_budget": self.dropped_over_turn_budget,
            "kept": self.kept,
        }

    def summary(self) -> str:
        return (
            f"{self.considered} trajectory(ies) considered -> {self.kept} kept "
            f"(dropped: {self.dropped_unverified} unverified, "
            f"{self.dropped_no_steps} empty, "
            f"{self.dropped_over_turn_budget} over the "
            f"{TURN_BUDGET_FACTOR}x-median turn budget)"
        )


@dataclass(frozen=True, slots=True)
class TaskBudget:
    """The turn budget derived for one task, and the numbers it came from."""

    task_id: str
    median_turns: float
    budget: float
    sample_size: int

    def allows(self, turn_count: int) -> bool:
        """Inclusive at the boundary.

        A trajectory sitting exactly on 1.5x the median is *kept*: "within 1.5x"
        reads inclusive, and the boundary lands on an exact value often enough to
        matter (a median of 4 gives a budget of 6.0, and six-turn runs are common).
        Turn counts are integers, so a median is whole or half and 1.5x of either
        is a multiple of 0.25 — always an exact binary float. The comparison has no
        rounding slop to worry about.
        """
        return turn_count <= self.budget


def turn_budgets(
    trajectories: Iterable[Trajectory], *, factor: float = TURN_BUDGET_FACTOR
) -> dict[str, TaskBudget]:
    """The per-task turn budget, computed over the **verified** runs of that task.

    Verified-only on purpose: a failing twelve-turn flail would otherwise raise the
    budget that is supposed to exclude flailing. The gate would then be loosest
    exactly on the tasks where the agent behaves worst.
    """
    by_task: dict[str, list[int]] = {}
    for traj in trajectories:
        if traj.verified and traj.steps:
            by_task.setdefault(traj.task_id, []).append(traj.turn_count)
    return {
        task_id: TaskBudget(
            task_id=task_id,
            median_turns=float(median(counts)),
            budget=factor * float(median(counts)),
            sample_size=len(counts),
        )
        for task_id, counts in sorted(by_task.items())
    }


def select(
    trajectories: Sequence[Trajectory], *, factor: float = TURN_BUDGET_FACTOR
) -> tuple[tuple[Trajectory, ...], FilterCounts, Mapping[str, TaskBudget]]:
    """Apply both gates. Returns the survivors, the drop counts, and the budgets.

    The budgets come back so a report can show *why* a run was dropped rather than
    only that it was — "12 turns against a 6.0 budget" is actionable, "dropped" is not.
    """
    budgets = turn_budgets(trajectories, factor=factor)
    kept: list[Trajectory] = []
    unverified = 0
    empty = 0
    over = 0
    for traj in trajectories:
        if not traj.verified:
            unverified += 1
            continue
        if not traj.steps:
            empty += 1
            continue
        # A verified, non-empty run always contributed to its own task's median, so
        # a missing budget is a bug in turn_budgets and must not be read as a drop.
        budget = budgets[traj.task_id]
        if not budget.allows(traj.turn_count):
            over += 1
            continue
        kept.append(traj)
    counts = FilterCounts(
        considered=len(trajectories),
        dropped_unverified=unverified,
        dropped_no_steps=empty,
        dropped_over_turn_budget=over,
        kept=len(kept),
    )
    return tuple(kept), counts, budgets


def budget_table(budgets: Mapping[str, TaskBudget]) -> list[dict[str, Any]]:
    """The budgets as plain rows, for a report or the demo. No formatting opinions."""
    return [
        {
            "task_id": b.task_id,
            "median_turns": b.median_turns,
            "budget": b.budget,
            "verified_runs": b.sample_size,
        }
        for b in budgets.values()
    ]


__all__ = [
    "TURN_BUDGET_FACTOR",
    "FilterCounts",
    "TaskBudget",
    "budget_table",
    "select",
    "turn_budgets",
]
