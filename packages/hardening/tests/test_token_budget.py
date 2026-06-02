from __future__ import annotations

import pytest

from ronin_hardening import (
    BudgetExceededError,
    TokenBudget,
    estimate_cost_usd,
)


# ---------- cost estimator ----------

def test_estimate_cost_known_model() -> None:
    # 1M input + 1M output on sonnet: $3 + $15 = $18
    assert estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000) == 18.0


def test_estimate_cost_unknown_model_is_zero() -> None:
    assert estimate_cost_usd("totally-fake", 1_000_000, 1_000_000) == 0.0


# ---------- charge + accumulation ----------

def test_charge_accumulates_usage() -> None:
    budget = TokenBudget(max_tokens=10_000, model="claude-sonnet-4-6")
    budget.charge(input_tokens=100, output_tokens=50)
    budget.charge(input_tokens=200, output_tokens=80)
    assert budget.used_input_tokens == 300
    assert budget.used_output_tokens == 130
    assert budget.used_tokens == 430
    assert budget.call_count == 2
    assert budget.remaining_tokens == 10_000 - 430


def test_charge_records_cost() -> None:
    budget = TokenBudget(model="claude-sonnet-4-6")
    budget.charge(input_tokens=1_000_000, output_tokens=0)
    assert budget.used_cost_usd == pytest.approx(3.0)
    budget.charge(input_tokens=0, output_tokens=1_000_000)
    assert budget.used_cost_usd == pytest.approx(18.0)


# ---------- limits ----------

def test_token_cap_raises() -> None:
    budget = TokenBudget(max_tokens=1_000)
    budget.charge(input_tokens=600, output_tokens=300)  # 900 ≤ 1000, fine
    with pytest.raises(BudgetExceededError) as exc_info:
        budget.charge(input_tokens=200, output_tokens=0)  # 1100 > 1000
    assert exc_info.value.used_tokens == 1100
    assert "token budget exceeded" in str(exc_info.value).lower()


def test_cost_cap_raises() -> None:
    budget = TokenBudget(max_cost_usd=1.0, model="claude-sonnet-4-6")
    # 200_000 input tokens at $3/M = $0.60; another 200_000 input = $1.20 > $1
    budget.charge(input_tokens=200_000, output_tokens=0)  # under
    with pytest.raises(BudgetExceededError) as exc_info:
        budget.charge(input_tokens=200_000, output_tokens=0)
    assert "cost budget exceeded" in str(exc_info.value).lower()
    assert exc_info.value.used_cost_usd > 1.0


def test_no_caps_never_raises() -> None:
    """A budget object with neither cap is a free counter."""
    budget = TokenBudget()
    for _ in range(1_000):
        budget.charge(input_tokens=10_000, output_tokens=5_000)
    # Didn't raise, even at 15M tokens
    assert budget.used_tokens == 15_000_000


# ---------- check_before ----------

def test_check_before_blocks_when_exhausted() -> None:
    budget = TokenBudget(max_tokens=100)
    budget.used_input_tokens = 200  # simulate prior exhaustion
    with pytest.raises(BudgetExceededError):
        budget.check_before()


def test_check_before_passes_with_room_left() -> None:
    budget = TokenBudget(max_tokens=10_000)
    budget.charge(input_tokens=100, output_tokens=50)
    budget.check_before()  # no raise
