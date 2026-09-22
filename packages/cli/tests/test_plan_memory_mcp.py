"""Planner repair, memory replacement, and MCP config checks."""
from pathlib import Path

from ronin_agent_patterns.plan_cache import Plan
from ronin_agent_patterns.task_engine import repair
from ronin_cli import memory_store
from ronin_cli.mcp_client import add_mcp_server, add_remote_mcp_server, build_mcp_tools, set_mcp_enabled
from ronin_cli.plugin_trust import is_trusted


def test_repair_appends_the_failure(tmp_path: Path) -> None:
    plan = repair(Plan(goal="ship", steps=["[implement/low] write it"]), "tests failed")
    assert plan.steps[-1].startswith("[implement/low] Repair after failure:")
    assert "tests failed" in plan.steps[-1]


def test_replace_memory_keeps_the_old_fact_when_the_new_one_is_a_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    assert memory_store.add_memory("I use Groq")
    assert memory_store.replace_memory("I use Groq", "ghp_" + "a" * 36) is False
    assert memory_store.list_memories() == ["I use Groq"]


def test_replace_memory_swaps_a_normal_fact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    assert memory_store.add_memory("I use Groq")
    assert memory_store.replace_memory("I use Groq", "I use Ollama")
    assert memory_store.list_memories() == ["I use Ollama"]


def test_mcp_rejects_a_bad_url_and_skips_disabled_servers(tmp_path: Path, monkeypatch) -> None:
    try:
        add_remote_mcp_server("docs", "ftp://example.com", root=tmp_path)
    except ValueError as exc:
        assert "http" in str(exc)
    else:
        raise AssertionError("ftp url was accepted")
    add_mcp_server("local", "echo", [], root=tmp_path)
    assert set_mcp_enabled("local", False, root=tmp_path)
    assert is_trusted(tmp_path / ".ronin" / "mcp.json")

    def boom(self):  # noqa: ANN001
        raise AssertionError("disabled server was spawned")

    monkeypatch.setattr("ronin_cli.mcp_client.MCPClient.start", boom)
    assert build_mcp_tools(tmp_path) == []
