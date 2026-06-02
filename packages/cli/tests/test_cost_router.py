"""Tests for the Cost Router — pricing, the savings ledger, cross-provider routing."""
from __future__ import annotations

from ronin_cli.config import RoninConfig
from ronin_cli.cost import CostLedger, estimate_cost, is_free, price_for
from ronin_cli.routing import route_decision


def test_free_providers_cost_nothing() -> None:
    assert is_free("cerebras", "gpt-oss-120b")
    assert is_free("groq", "llama-3.3-70b-versatile")
    assert estimate_cost("gemini", "gemini-flash-latest", 100_000, 50_000) == 0.0


def test_anthropic_priced() -> None:
    # 1M in + 1M out on sonnet = 3 + 15 = $18
    assert estimate_cost("anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000) == 18.0
    # opus override is pricier
    assert price_for("anthropic", "claude-opus-4-8") == (15.0, 75.0)


def test_free_model_suffix_override() -> None:
    # a :free OpenRouter model is free even though the provider table might not be
    assert is_free("openrouter", "qwen/qwen3-coder:free")


def test_ledger_tracks_savings() -> None:
    led = CostLedger(baseline_provider="anthropic", baseline_model="claude-sonnet-4-6")
    # a simple turn routed to a free provider
    led.record("cerebras", "gpt-oss-120b", 10_000, 2_000)
    # a complex turn on the strong model
    led.record("anthropic", "claude-sonnet-4-6", 10_000, 2_000)
    assert led.turns == 2
    assert led.free_turns == 1
    assert led.spent > 0.0                    # the anthropic turn cost something
    assert led.saved > 0.0                    # the free turn would've cost on baseline
    assert "saved $" in led.summary()


def test_route_decision_off_by_default() -> None:
    assert route_decision(RoninConfig(), "fix the bug") is None


def test_route_decision_cross_provider() -> None:
    cfg = RoninConfig(provider="anthropic",
                    route_fast="cerebras:gpt-oss-120b",
                    route_strong="anthropic:claude-sonnet-4-6")
    simple = route_decision(cfg, "hey")
    assert simple.tier == "simple" and simple.provider == "cerebras" and simple.free

    complex_ = route_decision(cfg, "refactor the auth module and fix the failing test")
    assert complex_.tier == "complex" and complex_.provider == "anthropic" and not complex_.free


def test_route_decision_bare_model_keeps_provider() -> None:
    cfg = RoninConfig(provider="groq", route_fast="llama-3.1-8b-instant",
                    route_strong="llama-3.3-70b-versatile")
    d = route_decision(cfg, "hello")
    assert d.provider == "groq" and d.model == "llama-3.1-8b-instant"
