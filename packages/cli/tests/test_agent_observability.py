from __future__ import annotations

from ronin_cli.agent_observability import dashboard_counts, list_agent_runs, provider_observations
from ronin_cli.agent_state import AgentTaskStateStore


def test_project_dashboard_aggregates_observed_provider_results(tmp_path) -> None:
    store = AgentTaskStateStore(tmp_path)
    first = store.create("first goal", selection={"profiles": ["researcher"]}, workflow={}, governance={})
    store.finish(
        first, success=True, output="ok", error=None,
        provider_health={"provider-a": {"attempts": 2, "failures": 0, "roles": ["researcher"]}},
    )
    second = store.create("second goal", selection={"profiles": ["tester"]}, workflow={}, governance={})
    store.finish(
        second, success=False, output="", error="timeout",
        provider_health={"provider-a": {"attempts": 1, "failures": 1, "roles": ["tester"], "last_error": "timeout"}},
    )

    runs = list_agent_runs(tmp_path)
    assert len(runs) == 2
    assert dashboard_counts(tmp_path) == {"completed": 1, "failed": 1}
    health = provider_observations(tmp_path)["provider-a"]
    assert health["attempts"] == 3
    assert health["failures"] == 1
    assert health["successful_attempts"] == 2
    assert health["status"] == "degraded"
    assert health["roles"] == ["researcher", "tester"]
