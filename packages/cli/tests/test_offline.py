"""Tests for fully-offline mode: local-brain enforcement + network-tool stripping."""
from __future__ import annotations

from ro_claude_kit_agent_patterns import Tool

from ro_claude_kit_cli import offline
from ro_claude_kit_cli.config import CSKConfig


def _tool(name: str) -> Tool:
    return Tool(name=name, description="x", input_schema={"type": "object", "properties": {}},
                handler=lambda: "ok")


def test_is_local_provider() -> None:
    assert offline.is_local_provider(CSKConfig(provider="ollama")) is True
    assert offline.is_local_provider(CSKConfig(provider="anthropic")) is False
    # a custom provider pointed at localhost counts as local
    local_custom = CSKConfig(provider="custom", base_url="http://localhost:1234/v1")
    assert offline.is_local_provider(local_custom) is True
    remote_custom = CSKConfig(provider="custom", base_url="https://api.example.com/v1")
    assert offline.is_local_provider(remote_custom) is False


def test_apply_offline_switches_cloud_to_ollama() -> None:
    cfg = CSKConfig(provider="anthropic", anthropic_api_key="sk-x", offline=True,
                    failover=[{"provider": "gemini"}])
    out = offline.apply_offline(cfg)
    assert out.provider == "ollama"
    assert out.failover == []  # cloud fallbacks would defeat the guarantee


def test_apply_offline_keeps_local_provider() -> None:
    cfg = CSKConfig(provider="ollama", model="llama3.1", offline=True)
    out = offline.apply_offline(cfg)
    assert out.provider == "ollama"
    assert out.resolved_model() == "llama3.1"


def test_apply_offline_noop_when_not_offline() -> None:
    cfg = CSKConfig(provider="anthropic", offline=False)
    assert offline.apply_offline(cfg) is cfg


def test_strip_network_tools() -> None:
    tools = [_tool("read_file"), _tool("web_search"), _tool("fetch_url"),
             _tool("edit_file"), _tool("generate_image")]
    kept = {t.name for t in offline.strip_network_tools(tools)}
    assert kept == {"read_file", "edit_file"}


def test_build_provider_forces_local_when_offline(monkeypatch) -> None:
    """An offline config with a cloud provider must yield a LOCAL provider."""
    from ro_claude_kit_agent_patterns import OpenAICompatProvider

    from ro_claude_kit_cli.runner import build_provider

    cfg = CSKConfig(provider="anthropic", anthropic_api_key="sk-x", offline=True)
    prov = build_provider(cfg)
    # ollama is served via the OpenAI-compatible provider, pointed at localhost
    assert isinstance(prov, OpenAICompatProvider)
    assert "localhost" in prov.base_url
