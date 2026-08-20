"""Unit coverage for the schema-first issue-to-PR mission contract."""
from __future__ import annotations

import pytest

from ronin_cli.mission_models import (
    MissionBudget,
    MissionRecord,
    MissionSpec,
    MissionStage,
    MissionUsage,
    can_transition,
    transition_record,
)


def _record() -> MissionRecord:
    return MissionRecord(
        id="mission-20260802-120000-abcdef",
        spec=MissionSpec(title="Add retry coverage", issue_text="Cover the retry failure path."),
    )


def test_mission_transitions_follow_the_reviewed_issue_to_pr_path() -> None:
    record = _record()
    assert can_transition(record.stage, MissionStage.INSPECTING)
    inspecting = transition_record(record, MissionStage.INSPECTING)
    planning = transition_record(inspecting, MissionStage.PLANNING)
    implementing = transition_record(planning, MissionStage.IMPLEMENTING)

    assert [record.stage, inspecting.stage, planning.stage, implementing.stage] == [
        MissionStage.PENDING,
        MissionStage.INSPECTING,
        MissionStage.PLANNING,
        MissionStage.IMPLEMENTING,
    ]


def test_mission_transition_rejects_skipping_required_work() -> None:
    with pytest.raises(ValueError, match="cannot transition"):
        transition_record(_record(), MissionStage.TESTING)


def test_mission_budget_is_hard_and_reports_every_exceeded_ceiling() -> None:
    budget = MissionBudget(
        max_tokens=100,
        max_cost_usd=1.0,
        max_wall_time_seconds=10,
        max_tool_calls=2,
        max_repair_attempts=1,
    )
    usage = MissionUsage(
        tokens=101,
        cost_usd=1.01,
        wall_time_seconds=10.1,
        tool_calls=3,
        repair_attempts=2,
    )

    assert usage.exceeded(budget) == (
        "tokens",
        "cost_usd",
        "wall_time_seconds",
        "tool_calls",
        "repair_attempts",
    )
