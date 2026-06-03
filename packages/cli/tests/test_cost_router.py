"""Tests for the Cost Router — pricing, the savings ledger, cross-provider routing."""
from __future__ import annotations

from ronin_cli.config import RoninConfig
from ronin_cli.cost import CostLedger, estimate_cost, is_free, price_for
from ronin_cli.routing import baseline_for, route_decision, route_turn_config


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


def test_route_turn_config_off_returns_same_config() -> None:
    cfg = RoninConfig(provider="anthropic")
    turn_cfg, decision = route_turn_config(cfg, "fix the bug")
    assert turn_cfg is cfg and decision is None


def test_route_turn_config_switches_provider() -> None:
    cfg = RoninConfig(provider="anthropic",
                      route_fast="cerebras:gpt-oss-120b",
                      route_strong="anthropic:claude-sonnet-4-6")
    turn_cfg, decision = route_turn_config(cfg, "hey")   # simple → free blade
    assert decision.tier == "simple"
    assert turn_cfg.provider == "cerebras"
    assert turn_cfg.resolved_model() == "gpt-oss-120b"
    # a complex turn lands on the strong blade
    turn_cfg2, decision2 = route_turn_config(cfg, "refactor the auth module and add tests")
    assert turn_cfg2.provider == "anthropic" and decision2.tier == "complex"


def test_baseline_for() -> None:
    # routing on → baseline is the strong target
    cfg = RoninConfig(provider="cerebras", route_fast="cerebras:gpt-oss-120b",
                      route_strong="anthropic:claude-sonnet-4-6")
    assert baseline_for(cfg) == ("anthropic", "claude-sonnet-4-6")
    # routing off → baseline is the current provider/model
    plain = RoninConfig(provider="anthropic")
    assert baseline_for(plain) == ("anthropic", "claude-sonnet-4-6")


def test_status_line_budget_mode() -> None:
    from ronin_cli.code_mode import _status_line

    class _R:
        usage = {"input_tokens": 50_000, "output_tokens": 3_000}
        streamed = False
    led = CostLedger(baseline_provider="anthropic", baseline_model="claude-sonnet-4-6")
    led.record("anthropic", "claude-sonnet-4-6", 50_000, 3_000)
    out = _status_line(RoninConfig(provider="anthropic"), _R(), 0.5, ledger=led, budget=0.10)
    assert "budget" in out and "over!" in out          # spent > cap → over
    # under budget → no "over"
    led2 = CostLedger(baseline_provider="anthropic", baseline_model="claude-sonnet-4-6")
    led2.record("anthropic", "claude-sonnet-4-6", 1_000, 100)
    assert "over!" not in _status_line(RoninConfig(), _R(), 0.1, ledger=led2, budget=5.0)


def test_ledger_status_line_shows_savings() -> None:
    # end-to-end: route a free turn + a strong turn, ledger reports savings
    led = CostLedger(baseline_provider="anthropic", baseline_model="claude-sonnet-4-6")
    led.record("cerebras", "gpt-oss-120b", 20_000, 4_000)   # free turn
    led.record("anthropic", "claude-sonnet-4-6", 20_000, 4_000)
    summary = led.summary()
    assert "saved $" in summary and "1/2 turns free" in summary
