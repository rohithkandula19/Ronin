"""Deterministic coverage for repository routing, contracts, governance, and state."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_agent_patterns import OrchestrationPlan, OrchestratorAgent, OrchestratorSubAgent, Step, Subtask
from ronin_cli.agent_catalog import build_catalog, repository_signals, select_profiles_for_repository
from ronin_cli.agent_control_eval import run_agent_control_eval
from ronin_cli.agent_governance import OrchestrationGovernance, provider_health
from ronin_cli.agent_state import AgentTaskStateStore, load_agent_task_state
from ronin_cli.agent_workflow import workflow_for_goal
from ronin_cli.config import RoninConfig
from ronin_cli.orchestrate import core_agent_profiles
from ronin_cli.main import app


def test_repository_signals_route_a_specialist_without_task_language(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    (tmp_path / "service.py").write_text("def authorize_request():\n    return True\n", encoding="utf-8")

    signals = repository_signals("improve request handling", tmp_path)
    selected = select_profiles_for_repository(
        "improve request handling",
        build_catalog(core_agent_profiles(), tmp_path),
        core_keys=(profile.key for profile in core_agent_profiles()),
        repository=signals,
        max_agents=8,
    )

    assert "python" in signals.tags
    assert "service.py" in signals.relevant_files
    assert any(
        any(reason.startswith("repo:") for reason in reasons)
        for key, reasons in selected.reasons.items()
        if key not in {profile.key for profile in core_agent_profiles()}
    )


def test_write_workflow_requires_separate_handoffs() -> None:
    workflow = workflow_for_goal("add an endpoint", write=True)
    orch = OrchestratorAgent(
        sub_agents=[
            OrchestratorSubAgent(role=role, description=role, system=role)
            for role in workflow.required_roles
        ],
        required_roles=set(workflow.required_roles),
        role_dependencies={role: set(upstream) for role, upstream in workflow.role_dependencies.items()},
        acceptance_roles=set(workflow.acceptance_roles),
    )
    valid = OrchestrationPlan(
        goal="g",
        subtasks=[
            Subtask(id="research", description="inspect", assignee="researcher"),
            Subtask(id="impl", description="change", assignee="implementer", depends_on=["research"]),
            Subtask(id="review", description="review", assignee="reviewer", depends_on=["impl"]),
            Subtask(id="test", description="verify", assignee="tester", depends_on=["impl"]),
        ],
    )
    orch._validate_plan(valid)

    invalid = valid.model_copy(update={
        "subtasks": [
            *valid.subtasks[:2],
            Subtask(id="review", description="review", assignee="reviewer"),
            valid.subtasks[3],
        ],
    })
    with pytest.raises(ValueError, match="must directly depend"):
        orch._validate_plan(invalid)


def test_core_rejects_a_plan_that_exceeds_iteration_reservation() -> None:
    orch = OrchestratorAgent(
        sub_agents=[
            OrchestratorSubAgent(role="a", description="a", system="a", max_iterations=8),
            OrchestratorSubAgent(role="b", description="b", system="b", max_iterations=8),
        ],
        max_total_subtask_iterations=12,
    )
    plan = OrchestrationPlan(
        goal="g",
        subtasks=[
            Subtask(id="a", description="a", assignee="a"),
            Subtask(id="b", description="b", assignee="b"),
        ],
    )
    with pytest.raises(ValueError, match="reserves 16"):
        orch._validate_plan(plan)


def test_governance_validates_limits_and_observes_provider_failures() -> None:
    governance = OrchestrationGovernance(
        max_agents=4,
        max_parallel=2,
        max_iterations_per_agent=5,
        max_total_subtask_iterations=20,
        max_wall_time_seconds_per_agent=30,
    )
    governance.validate_team(4)
    with pytest.raises(ValueError, match="above governance"):
        governance.validate_team(5)
    health = provider_health([
        {"assignee": "researcher", "provider": "model-a", "success": True},
        {"assignee": "tester", "provider": "model-b", "success": False, "error": "timeout"},
    ])
    assert health["model-a"]["status"] == "healthy"
    assert health["model-b"] == {
        "attempts": 1,
        "failures": 1,
        "last_error": "timeout",
        "roles": ["tester"],
        "status": "degraded",
    }


def test_agent_task_state_is_atomic_and_tracks_live_events(tmp_path: Path) -> None:
    store = AgentTaskStateStore(tmp_path)
    state = store.create(
        "ship the feature",
        selection={"profiles": ["researcher"]},
        workflow={"name": "investigation-v1"},
        governance={"max_agents": 4},
    )
    store.apply_step(state, Step(kind="plan", content={"subtasks": [{"id": "a"}]}))
    store.apply_step(state, Step(
        kind="tool_result",
        content={"subtask": "a", "assignee": "researcher", "success": True, "output": "found it"},
        metadata={"iterations": 2},
    ))
    store.finish(
        state, success=True, output="done", error=None,
        provider_health={"researcher": {"status": "healthy"}},
    )

    restored = load_agent_task_state(tmp_path, state["id"])
    assert restored is not None
    assert restored["status"] == "completed"
    assert restored["subtasks"]["a"]["iterations"] == 2
    assert restored["output"] == "done"


def test_agent_control_eval_fixture_suite_passes() -> None:
    outcomes = run_agent_control_eval()
    assert outcomes
    assert all(outcome.passed for outcome in outcomes)


def test_agent_control_eval_cli_needs_no_provider_credentials() -> None:
    result = CliRunner().invoke(app, ["eval", "agents"])
    assert result.exit_code == 0, result.stdout
    assert "3/3 passed" in result.stdout


def test_agent_platform_cli_queue_dashboard_sandbox_and_eval(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    queued = runner.invoke(app, [
        "util", "agent-queue", "add", "inspect retries", "--write", "--root", str(tmp_path),
    ])
    assert queued.exit_code == 0, queued.stdout
    listed = runner.invoke(app, ["util", "agent-queue", "list", "--root", str(tmp_path)])
    assert listed.exit_code == 0, listed.stdout
    assert "inspect retries" in listed.stdout

    store = AgentTaskStateStore(tmp_path)
    state = store.create("inspect retries", selection={"profiles": ["researcher"]}, workflow={}, governance={})
    store.finish(
        state, success=True, output="done", error=None,
        provider_health={"local:model": {"attempts": 1, "failures": 0, "roles": ["researcher"]}},
    )
    dashboard = runner.invoke(app, ["util", "agent-runs", "--root", str(tmp_path)])
    assert dashboard.exit_code == 0, dashboard.stdout
    assert "provider observations" in dashboard.stdout
    assert "local:model" in dashboard.stdout

    monkeypatch.setenv("RONIN_BACKEND", "docker:agent")
    sandbox = runner.invoke(app, ["util", "sandbox"])
    assert sandbox.exit_code == 0, sandbox.stdout
    assert "fail closed" in sandbox.stdout

    platform = runner.invoke(app, ["eval", "platform"])
    assert platform.exit_code == 0, platform.stdout
    assert "12/12 passed" in platform.stdout


def test_queued_offline_worker_uses_embedded_provider(tmp_path: Path, monkeypatch) -> None:
    import ronin_cli.main as main_module
    import ronin_cli.orchestrate as orchestrate_module

    runner = CliRunner()
    assert runner.invoke(app, ["util", "agent-queue", "add", "inspect retry", "--root", str(tmp_path)]).exit_code == 0
    seen = {}

    def fake_run(config, goal, **kwargs):
        seen["provider"] = config.provider
        seen["offline"] = config.offline
        seen["goal"] = goal
        return SimpleNamespace(success=True, error=None, run_id="agent-1", output="done")

    monkeypatch.setattr(main_module, "load_config", lambda: RoninConfig(provider="ollama"))
    monkeypatch.setattr(orchestrate_module, "run_orchestrate", fake_run)
    result = runner.invoke(app, ["util", "agent-queue", "run-next", "--offline", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert seen == {"provider": "local", "offline": True, "goal": "inspect retry"}
