from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from ronin_cli.agent_catalog import AgentProfile
from ronin_cli.agent_operations import operations_snapshot
from ronin_cli.agent_recovery import load_recovery_context, recover_profiles
from ronin_cli.agent_state import AgentTaskStateStore
from ronin_cli.agent_routing import ModelCandidate, route_profiles
from ronin_cli.code_tools import build_code_tools
from ronin_cli.config import RoninConfig
from ronin_cli.main import app
from ronin_cli.model_scorecards import ModelScorecardStore, apply_scorecards
from ronin_cli.provider_health_store import ProviderHealthStore


def _failed_state(root: Path) -> dict:
    store = AgentTaskStateStore(root)
    state = store.create(
        "repair retries",
        selection={"profiles": ["researcher", "implementer"]},
        workflow={}, governance={},
        execution={"roster_spec": "implementer=local", "read_only": False},
    )
    state["status"] = "failed"
    state["plan"] = [{"id": "research"}, {"id": "implement"}, {"id": "test"}]
    state["subtasks"] = {
        "research": {"status": "completed", "success": True},
        "implement": {"status": "failed", "success": False},
    }
    store.save(state)
    return state


def test_recovery_context_keeps_team_and_only_persists_status_handoff(tmp_path: Path) -> None:
    state = _failed_state(tmp_path)

    context = load_recovery_context(tmp_path, state["id"])

    assert context.goal == "repair retries"
    assert context.roster_spec == "implementer=local"
    assert context.write is True
    assert context.completed == ("research",)
    assert context.failed == ("implement",)
    assert context.unfinished == ("test",)
    assert "recovery run linked" in context.planner_context()
    profiles = (
        AgentProfile("researcher", "reads", "read", ("read",)),
        AgentProfile("implementer", "writes", "write", ("write",), tier="write"),
    )
    assert [profile.key for profile in recover_profiles(tmp_path, context, profiles)] == ["researcher", "implementer"]
    with pytest.raises(ValueError, match="no longer available"):
        recover_profiles(tmp_path, context, profiles[:1])


def test_recovery_refuses_completed_state(tmp_path: Path) -> None:
    store = AgentTaskStateStore(tmp_path)
    state = store.create("done", selection={"profiles": ["researcher"]}, workflow={}, governance={})
    store.finish(state, success=True, output="done", error=None, provider_health={})

    with pytest.raises(ValueError, match="completed"):
        load_recovery_context(tmp_path, state["id"])


def test_durable_provider_health_cools_down_then_recovers(tmp_path: Path) -> None:
    store = ProviderHealthStore(tmp_path)
    store.observe({"alpha:model": {"attempts": 1, "failures": 1, "roles": ["tester"], "last_error": "timeout"}}, now=100)
    cooling = store.view(now=101)["alpha:model"]
    assert cooling["status"] == "cooling-down"
    assert cooling["failures"] == 1

    store.observe({"alpha:model": {"attempts": 1, "failures": 0, "roles": ["tester"]}}, now=200)
    recovered = store.view(now=201)["alpha:model"]
    assert recovered["status"] == "healthy"
    assert recovered["attempts"] == 2
    assert recovered["failures"] == 1

    mixed = ProviderHealthStore(tmp_path / "mixed")
    mixed.observe({"beta:model": {"attempts": 2, "failures": 1, "roles": ["tester"]}}, now=300)
    assert mixed.view(now=301)["beta:model"]["status"] == "cooling-down"


def test_scorecard_import_guides_explicit_model_routing(tmp_path: Path) -> None:
    report = tmp_path / "swe.json"
    report.write_text(json.dumps({"model": "cheap", "summary": {"resolved_rate": 0.93, "total": 10}}), encoding="utf-8")
    cards = ModelScorecardStore(tmp_path)
    card = cards.import_report(report)
    assert card.quality == .93
    candidates = apply_scorecards(
        [ModelCandidate("cheap", quality=.5, cost=.05), ModelCandidate("strong", quality=.85, cost=.8)],
        cards.list(),
    )
    writer = AgentProfile("writer", "writes", "write", ("code",), tier="write")
    decision = route_profiles([writer], candidates)[0]
    assert decision.spec == "cheap"


def test_operations_snapshot_joins_queue_recovery_ledger_and_scorecards(tmp_path: Path) -> None:
    _failed_state(tmp_path)
    from ronin_cli.agent_queue import AgentQueue
    from ronin_cli.autonomy_ledger import AutonomyLedger
    from ronin_cli.model_scorecards import ModelScorecard

    AgentQueue(tmp_path).enqueue("do work")
    AutonomyLedger(tmp_path).append("test", run_id="r1", goal="private", payload={"success": True})
    ModelScorecardStore(tmp_path).upsert([ModelScorecard("local", .8, "test")])

    snapshot = operations_snapshot(tmp_path)

    assert snapshot["queue"] == {"queued": 1}
    assert len(snapshot["recoverable"]) == 1
    assert snapshot["ledger"].valid is True
    assert snapshot["scorecards"]["local"].quality == .8
    assert snapshot["proposals"] == ()
    assert snapshot["fleet_plans"] == ()
    assert snapshot["fleet_runs"] == ()


def test_json_and_toml_invalid_writes_are_rejected_before_disk_mutation(tmp_path: Path) -> None:
    tools = {tool.name: tool.handler for tool in build_code_tools(tmp_path)}

    json_message = tools["write_file"]("package.json", "{invalid")
    toml_message = tools["write_file"]("pyproject.toml", "[project\nname = 'broken'")

    assert json_message.startswith("ERROR: patch verification failed")
    assert toml_message.startswith("ERROR: patch verification failed")
    assert not (tmp_path / "package.json").exists()
    assert not (tmp_path / "pyproject.toml").exists()


def test_cli_recovery_starts_a_linked_replacement_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ronin_cli.main as main_module
    import ronin_cli.orchestrate as orchestrate_module
    from typer.testing import CliRunner

    state = _failed_state(tmp_path)
    seen: dict[str, object] = {}

    def fake_run(config, goal, **kwargs):
        seen["provider"] = config.provider
        seen["goal"] = goal
        seen["resume_from"] = kwargs["resume_from"]
        seen["summary"] = kwargs["resume_summary"]
        return SimpleNamespace(success=True, error=None, run_id="agent-replacement", output="recovered")

    monkeypatch.setattr(main_module, "load_config", lambda: RoninConfig(provider="local"))
    monkeypatch.setattr(orchestrate_module, "run_orchestrate", fake_run)
    result = CliRunner().invoke(app, ["util", "agent-recover", state["id"], "--root", str(tmp_path), "--offline"])

    assert result.exit_code == 0, result.stdout
    assert seen["provider"] == "local"
    assert seen["goal"] == "repair retries"
    assert seen["resume_from"] == state["id"]
    assert "implement" in str(seen["summary"])
