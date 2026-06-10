"""Edge-case coverage for cost.py pricing + ledger."""
from __future__ import annotations

import pytest

from ronin_cli.cost import CostLedger, estimate_cost, is_free, price_for


@pytest.mark.parametrize("provider,model,free", [
    ("cerebras", "gpt-oss-120b", True),
    ("groq", "llama-3.3-70b", True),
    ("gemini", "gemini-flash-latest", True),
    ("ollama", "llama3.1", True),
    ("openrouter", "qwen/qwen3-coder:free", True),
    ("anthropic", "claude-sonnet-4-6", False),
    ("openai", "gpt-4o", False),
])
def test_is_free_matrix(provider, model, free) -> None:
    assert is_free(provider, model) is free


def test_model_overrides() -> None:
    assert price_for("anthropic", "claude-opus-4-8") == (15.0, 75.0)
    assert price_for("anthropic", "claude-haiku-4-5") == (0.80, 4.0)
    assert price_for("openai", "gpt-4o") == (2.5, 10.0)


def test_free_suffix_overrides_provider_table() -> None:
    # a :free model is free even on a non-free provider name
    assert is_free("anthropic", "something:free")


def test_unknown_provider_fallback_not_free() -> None:
    assert not is_free("mystery", "some-model")
    assert price_for("mystery", "x") == (1.0, 3.0)


def test_estimate_cost_zero_tokens() -> None:
    assert estimate_cost("anthropic", "claude-sonnet-4-6", 0, 0) == 0.0


def test_estimate_cost_input_output_split() -> None:
    # 2M input @ $3, 0 output → $6
    assert estimate_cost("anthropic", "claude-sonnet-4-6", 2_000_000, 0) == 6.0


def test_ledger_by_provider_accumulates() -> None:
    led = CostLedger()
    led.record("anthropic", "claude-sonnet-4-6", 1_000_000, 0)
    led.record("anthropic", "claude-sonnet-4-6", 1_000_000, 0)
    assert led.turns == 2
    assert led.spent == 6.0


def test_ledger_empty_summary() -> None:
    assert "0.00" in CostLedger().summary()


def test_ledger_last_tracked() -> None:
    led = CostLedger(baseline_provider="anthropic", baseline_model="claude-sonnet-4-6")
    led.record("cerebras", "gpt-oss-120b", 1_000_000, 1_000_000)
    assert led.last_actual == 0.0          # free
    assert led.last_baseline == 18.0       # what sonnet would've cost
