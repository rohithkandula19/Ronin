"""Tests for Scout → Strike blade resolution."""
from __future__ import annotations

from ro_claude_kit_cli.config import CSKConfig
from ro_claude_kit_cli.scout import resolve_blades


def test_no_routing_returns_none() -> None:
    assert resolve_blades(CSKConfig()) is None
    assert resolve_blades(CSKConfig(route_fast="cerebras:gpt-oss-120b")) is None  # need both


def test_cross_provider_blades() -> None:
    cfg = CSKConfig(provider="anthropic",
                    route_fast="cerebras:gpt-oss-120b",
                    route_strong="anthropic:claude-sonnet-4-6")
    blades = resolve_blades(cfg)
    assert blades is not None
    assert blades.scout.provider == "cerebras"
    assert blades.scout.resolved_model() == "gpt-oss-120b"
    assert blades.strike.provider == "anthropic"
    assert "claude-sonnet" in blades.strike.resolved_model()
    assert "cerebras" in blades.scout_label and "anthropic" in blades.strike_label


def test_bare_model_keeps_current_provider() -> None:
    cfg = CSKConfig(provider="groq",
                    route_fast="llama-3.1-8b-instant",
                    route_strong="llama-3.3-70b-versatile")
    blades = resolve_blades(cfg)
    assert blades.scout.provider == "groq" and blades.strike.provider == "groq"
    assert blades.scout.resolved_model() == "llama-3.1-8b-instant"
