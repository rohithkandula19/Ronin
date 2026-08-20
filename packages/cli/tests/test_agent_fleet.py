"""Tests for bounded, persisted specialist fleet plans."""
from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from ronin_cli.agent_catalog import AgentProfile, RepositorySignals
from ronin_cli.agent_fleet import FleetPlanStore, plan_fleet
from ronin_cli.main import app


def _profiles() -> tuple[AgentProfile, ...]:
    return (
        AgentProfile("researcher", "research", "research", ("research",)),
        AgentProfile("implementer", "implement", "implement", ("implementation",), tier="write"),
        AgentProfile("reviewer", "review", "review", ("review",)),
        AgentProfile("tester", "test", "test", ("testing",), tier="test"),
        AgentProfile("payments-investigator", "payments", "investigate", ("payments",)),
        AgentProfile("payments-implementer", "payments", "implement", ("payments",), tier="write"),
        AgentProfile("payments-quality-engineer", "payments", "verify", ("payments",), tier="test"),
    )


def _plan(*, write: bool) -> object:
    return plan_fleet(
        "improve payment reliability", _profiles(),
        core_keys=("researcher", "implementer", "reviewer", "tester"),
        repository=RepositorySignals(tags=("payments", "reliability")),
        write=write, max_profiles=7, max_parallel=2,
    )


def test_write_fleet_is_partitioned_into_safe_phase_waves() -> None:
    plan = _plan(write=True)

    assert [wave.phase for wave in plan.waves] == [
        "research", "implementation", "acceptance", "acceptance",
    ]
    assert all(wave.parallelism <= 2 for wave in plan.waves)
    assert plan.waves[1].waits_for == (1,)
    assert plan.waves[2].waits_for == (2,)
    assert plan.waves[3].waits_for == (2,)


def test_read_only_fleet_never_schedules_an_implementation_phase() -> None:
    plan = _plan(write=False)

    assert "implementation" not in [wave.phase for wave in plan.waves]
    assert "implementer" in plan.waves[0].profile_keys


def test_fleet_rejects_unbounded_profile_or_wave_requests() -> None:
    with pytest.raises(ValueError, match="max_profiles cannot exceed 512"):
        plan_fleet(
            "improve payment reliability", _profiles(),
            core_keys=("researcher", "implementer", "reviewer", "tester"),
            repository=RepositorySignals(tags=("payments",)),
            write=True, max_profiles=513,
        )
    with pytest.raises(ValueError, match="max_parallel"):
        plan_fleet(
            "improve payment reliability", _profiles(),
            core_keys=("researcher", "implementer", "reviewer", "tester"),
            repository=RepositorySignals(tags=("payments",)),
            write=True, max_parallel=33,
        )


def test_fleet_store_is_atomic_and_rejects_path_escape(tmp_path) -> None:
    plan = _plan(write=True)
    store = FleetPlanStore(tmp_path)
    path = store.save(plan)

    assert path.is_file()
    assert store.load(plan.id) == plan
    assert store.list() == (plan,)
    with pytest.raises(ValueError, match="invalid fleet plan id"):
        store.load("../" + plan.id)


def test_fleet_cli_plans_lists_and_shows_without_a_provider(tmp_path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, [
        "util", "fleet", "plan", "audit payment security", "--root", str(tmp_path),
        "--max-profiles", "12", "--max-parallel", "3", "--write",
    ])

    assert result.exit_code == 0, result.stdout
    match = re.search(r"fleet-\d{8}-\d{6}-[0-9a-f]{6}", result.stdout)
    assert match is not None
    plan_id = match.group(0)
    listed = runner.invoke(app, ["util", "fleet", "list", "--root", str(tmp_path)])
    assert listed.exit_code == 0, listed.stdout
    assert plan_id in listed.stdout
    shown = runner.invoke(app, ["util", "fleet", "show", plan_id, "--root", str(tmp_path)])
    assert shown.exit_code == 0, shown.stdout
    assert "fleet plan" in shown.stdout
