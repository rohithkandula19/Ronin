from __future__ import annotations

from ronin_cli.agent_platform_eval import run_agent_platform_eval


def test_agent_platform_eval_runs_offline() -> None:
    outcomes = run_agent_platform_eval()
    assert len(outcomes) == 7
    assert all(outcome.passed for outcome in outcomes)
