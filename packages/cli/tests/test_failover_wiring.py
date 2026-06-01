"""Tests that CSKConfig.failover composes into a FailoverProvider via build_provider,
and that per-spec configs resolve the right provider/model/key."""
from __future__ import annotations

from ro_claude_kit_agent_patterns import (
    AnthropicProvider,
    FailoverProvider,
    OpenAICompatProvider,
)

from ro_claude_kit_cli.config import CSKConfig
from ro_claude_kit_cli.runner import build_provider, config_for_spec


def test_no_failover_returns_plain_provider() -> None:
    cfg = CSKConfig(provider="anthropic", anthropic_api_key="sk-x")
    prov = build_provider(cfg)
    assert isinstance(prov, AnthropicProvider)


def test_failover_composes_chain_in_order() -> None:
    cfg = CSKConfig(
        provider="anthropic", anthropic_api_key="sk-x",
        failover=[
            {"provider": "gemini", "api_key": "g-key"},
            {"provider": "ollama"},
        ],
    )
    prov = build_provider(cfg)
    assert isinstance(prov, FailoverProvider)
    assert len(prov.providers) == 3
    # primary first, then the two fallbacks, in order
    assert isinstance(prov.providers[0], AnthropicProvider)
    assert isinstance(prov.providers[1], OpenAICompatProvider)  # gemini = openai-compat
    assert isinstance(prov.providers[2], OpenAICompatProvider)  # ollama
    assert prov.labels[0].startswith("anthropic:")
    assert prov.labels[1].startswith("gemini:")
    assert prov.labels[2].startswith("ollama:")


def test_config_for_spec_routes_key_to_right_slot() -> None:
    base = CSKConfig(provider="anthropic", anthropic_api_key="primary-key")

    anthropic_spec = config_for_spec(base, {"provider": "anthropic", "api_key": "k1"})
    assert anthropic_spec.anthropic_api_key == "k1"

    gemini_spec = config_for_spec(base, {
        "provider": "gemini", "model": "gemini-flash-latest", "api_key": "k2"})
    assert gemini_spec.openai_api_key == "k2"
    assert gemini_spec.resolved_model() == "gemini-flash-latest"
    # gemini's base_url comes from the preset
    assert "generativelanguage" in (gemini_spec.resolved_base_url() or "")


def test_config_for_spec_never_recurses_failover() -> None:
    base = CSKConfig(provider="anthropic", failover=[{"provider": "ollama"}])
    sub = config_for_spec(base, {"provider": "ollama"})
    assert sub.failover == []
