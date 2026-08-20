from __future__ import annotations

import pytest

from ronin_agent_patterns import AgentRequest, assemble_context
from ronin_cli.agent_context import build_code_context_providers
from ronin_cli.project_instincts import ProjectInstinctStore


def test_instincts_require_reinforcement_before_context_recall(tmp_path) -> None:
    store = ProjectInstinctStore(tmp_path)
    instinct_id = store.propose("Run focused tests before the full suite.", evidence="PR 18 caught a regression.")

    assert store.recall("how should we test") == []
    active = store.reinforce(instinct_id, evidence="PR 19 confirmed the same workflow.", weight=0.4)
    assert active["state"] == "active"
    assert active["observations"] == 2

    assembly = assemble_context(
        "Base policy.",
        AgentRequest(task="How should we test this change?"),
        build_code_context_providers(tmp_path, include_retrieval=False, retrieval_token_budget=800),
        max_context_chars=4_000,
    )
    assert "project-instincts" in assembly.sources
    assert "Run focused tests" in assembly.system_prompt
    assert "Runtime Context: project-instincts (untrusted)" in assembly.system_prompt


def test_instincts_expire_and_reject_secrets(tmp_path) -> None:
    store = ProjectInstinctStore(tmp_path)
    with pytest.raises(ValueError, match="possible secret"):
        store.propose("Use api_key=very-secret-token", evidence="Never store credentials.")

    instinct_id = store.propose("Use a temporary migration branch.", evidence="Observed in a dry run.", expires_in_days=1)
    with store._connect() as con:
        con.execute("UPDATE project_instincts SET expires_at = 0 WHERE id = ?", (instinct_id,))
    assert store.list() == []
    with pytest.raises(ValueError, match="expired"):
        store.reinforce(instinct_id, evidence="A later run cannot revive it.")
