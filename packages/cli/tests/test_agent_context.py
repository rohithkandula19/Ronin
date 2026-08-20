from __future__ import annotations

import pytest

from ronin_agent_patterns import AgentRequest, assemble_context

from ronin_cli.agent_context import build_code_context_providers
from ronin_cli.project_memory import ProjectMemory
from ronin_cli.repo_index import build_index, index_db_path


def test_existing_memory_and_repository_index_use_one_context_contract(tmp_path) -> None:
    (tmp_path / "RONIN.md").write_text("Always add a regression test.", encoding="utf-8")
    (tmp_path / "retry.py").write_text("def retry_request():\n    return True\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("def unrelated():\n    return False\n", encoding="utf-8")
    ProjectMemory(tmp_path).remember("Retry fixes require timeout coverage.", tags=["retries"])
    build_index(tmp_path, index_db_path(tmp_path))

    assembly = assemble_context(
        "Base policy.",
        AgentRequest(task="fix retry timeout"),
        build_code_context_providers(tmp_path, include_retrieval=True, retrieval_token_budget=800),
        max_context_chars=4_000,
    )

    assert assembly.sources == [
        "project-instructions:RONIN.md",
        "project-memory",
        "repository-index",
    ]
    assert "Always add a regression test." in assembly.system_prompt
    assert "Retry fixes require timeout coverage." in assembly.system_prompt
    assert "retry.py" in assembly.system_prompt
    assert "Runtime Context: project-memory (untrusted)" in assembly.system_prompt


def test_retrieval_provider_falls_back_without_a_persistent_index(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "retry.py").write_text("def retry_request():\n    return True\n", encoding="utf-8")
    monkeypatch.setattr(
        "ronin_cli.context_engine.relevant_context",
        lambda *_args, **_kwargs: "## Relevant code\n- `retry.py`\n",
    )

    assembly = assemble_context(
        "Base policy.",
        AgentRequest(task="fix retry"),
        build_code_context_providers(tmp_path, include_retrieval=True, retrieval_token_budget=800),
        max_context_chars=4_000,
    )

    assert "repository-retrieval" in assembly.sources
