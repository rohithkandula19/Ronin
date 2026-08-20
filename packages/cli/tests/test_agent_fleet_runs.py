"""Regression coverage for durable, dependency-gated fleet execution."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ronin_cli.agent_catalog import AgentProfile, RepositorySignals
from ronin_cli.agent_fleet import FleetPlanStore, plan_fleet
from ronin_cli.agent_fleet_runs import (
    FleetRunStore,
    FleetWaveResult,
    execute_next_wave,
)
from ronin_cli.main import app


def _plan():
    profiles = (
        AgentProfile("researcher", "research", "research", ("research",)),
        AgentProfile("implementer", "implement", "implement", ("implementation",), tier="write"),
        AgentProfile("reviewer", "review", "review", ("review",)),
        AgentProfile("tester", "test", "test", ("testing",), tier="test"),
    )
    return plan_fleet(
        "improve retry handling", profiles,
        core_keys=("researcher", "implementer", "reviewer", "tester"),
        repository=RepositorySignals(tags=("retries",)), write=True,
        max_profiles=4, max_parallel=2,
    )


def test_fleet_run_advances_one_dependency_ready_wave_at_a_time(tmp_path) -> None:
    store = FleetRunStore(tmp_path)
    created = store.create(_plan())

    claimed, first = store.claim_next(created.id)
    assert claimed.status == "running"
    assert first is not None and first.number == 1 and first.phase == "research"
    assert first.attempts == 1
    after_first = store.finish_wave(
        created.id, first.number, FleetWaveResult(True, agent_run_id="agent-research"),
    )
    assert after_first.status == "running"
    assert after_first.waves[0].agent_run_id == "agent-research"

    _claimed, second = store.claim_next(created.id)
    assert second is not None and second.number == 2 and second.phase == "implementation"
    failed = store.finish_wave(created.id, second.number, FleetWaveResult(False, error="provider timeout"))
    assert failed.status == "failed"
    assert failed.error == "provider timeout"
    assert failed.waves[2].status == "waiting"

    retried = store.retry_failed(created.id, second.number)
    assert retried.status == "queued"
    assert retried.waves[1].attempts == 1
    _claimed, second_retry = store.claim_next(created.id)
    assert second_retry is not None and second_retry.attempts == 2


def test_fleet_run_finishes_and_recovers_only_after_explicit_operator_action(tmp_path) -> None:
    store = FleetRunStore(tmp_path)
    created = store.create(_plan())

    _run, claimed = store.claim_next(created.id)
    assert claimed is not None
    recovered = store.recover_interrupted(created.id)
    assert recovered.status == "queued"
    assert recovered.waves[0].status == "waiting"
    assert recovered.waves[0].attempts == 1

    def worker(_run, wave):
        return FleetWaveResult(True, agent_run_id=f"agent-wave-{wave.number}")

    current = recovered
    while current.status != "completed":
        current, wave = execute_next_wave(store, current.id, worker)
        assert wave is not None
    assert [wave.agent_run_id for wave in current.waves] == [
        "agent-wave-1", "agent-wave-2", "agent-wave-3",
    ]
    assert current.waves[0].attempts == 2
    with pytest.raises(ValueError, match="already completed"):
        store.claim_next(current.id)


def test_fleet_run_rejects_path_escape_and_active_cancellation(tmp_path) -> None:
    store = FleetRunStore(tmp_path)
    created = store.create(_plan())
    with pytest.raises(ValueError, match="invalid fleet run id"):
        store.load("../" + created.id)
    store.claim_next(created.id)
    with pytest.raises(ValueError, match="while a wave is running"):
        store.cancel(created.id)


def test_fleet_cli_starts_and_runs_one_bounded_wave(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ronin_cli.main as main_module
    import ronin_cli.orchestrate as orchestrate_module
    from ronin_cli.config import RoninConfig

    plan = _plan()
    FleetPlanStore(tmp_path).save(plan)
    runner = CliRunner()
    started = runner.invoke(app, ["util", "fleet", "start", plan.id, "--root", str(tmp_path)])
    assert started.exit_code == 0, started.stdout
    run = FleetRunStore(tmp_path).list()[0]
    seen: dict[str, object] = {}

    def fake_run(config, goal, **kwargs):
        seen["provider"] = config.provider
        seen["goal"] = goal
        seen["profiles"] = [profile.key for profile in kwargs["profiles"]]
        seen["read_only"] = kwargs["read_only"]
        return SimpleNamespace(
            success=True, error=None, run_id="agent-wave-1", proposal=None, output="research complete",
        )

    monkeypatch.setattr(main_module, "load_config", lambda: RoninConfig(provider="local"))
    monkeypatch.setattr(orchestrate_module, "run_orchestrate", fake_run)
    result = runner.invoke(app, ["util", "fleet", "run-next", run.id, "--root", str(tmp_path), "--offline"])

    assert result.exit_code == 0, result.stdout
    assert seen["provider"] == "local"
    assert seen["profiles"] == ["researcher"]
    assert seen["read_only"] is True
    assert "Fleet execution: wave 1 (research)" in str(seen["goal"])
    assert FleetRunStore(tmp_path).load(run.id).waves[0].status == "completed"
