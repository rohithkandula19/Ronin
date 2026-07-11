from __future__ import annotations

import pytest

from ronin_hardening import (
    BudgetExceededError,
    TokenBudget,
    estimate_cost_usd,
    pricing_known,
)


# ---------- cost estimator ----------

def test_estimate_cost_known_model() -> None:
    # 1M input + 1M output on sonnet: $3 + $15 = $18
    assert estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000) == 18.0


def test_estimate_cost_unknown_model_is_none_not_zero() -> None:
    # Safety-critical: an unknown model must NOT be silently priced at $0
    # (that would let a cost cap pass forever). It reports UNKNOWN via None.
    assert estimate_cost_usd("totally-fake", 1_000_000, 1_000_000) is None
    assert not pricing_known("totally-fake")


def test_estimate_cost_free_and_local_models_are_zero() -> None:
    # Genuinely-free / local / :free models are known-$0, not unknown.
    assert estimate_cost_usd("gpt-oss-120b", 1_000_000, 1_000_000) == 0.0
    assert estimate_cost_usd("ollama", 1_000_000, 1_000_000) == 0.0
    assert estimate_cost_usd("deepseek/deepseek-v4:free", 1_000_000, 1_000_000) == 0.0
    assert pricing_known("gpt-oss-120b")
    assert pricing_known("some-model:free")


# ---------- fail-closed cost cap on UNKNOWN pricing (Stage A blocker 0.1) ----------

def test_cost_cap_fails_closed_when_pricing_unknown() -> None:
    # A $ cap over an unpriced model must halt, not run uncapped at a fake $0.
    budget = TokenBudget(max_cost_usd=1.0, model="totally-fake")
    with pytest.raises(BudgetExceededError):
        budget.check_before()
    with pytest.raises(BudgetExceededError):
        budget.charge(input_tokens=1_000_000, output_tokens=1_000_000)


def test_cost_cap_unknown_pricing_explicit_optin_runs() -> None:
    # Explicit opt-in is the documented escape hatch; the token cap still applies.
    budget = TokenBudget(max_cost_usd=1.0, model="totally-fake", allow_unknown_pricing=True)
    budget.check_before()          # no raise
    budget.charge(input_tokens=100, output_tokens=50)  # no raise, cost unenforced
    assert budget.used_cost_usd == 0.0


def test_no_cost_cap_unknown_pricing_is_fine() -> None:
    # With only a token cap (no $ cap), unknown pricing is not a problem.
    budget = TokenBudget(max_tokens=10_000, model="totally-fake")
    budget.check_before()
    budget.charge(input_tokens=100, output_tokens=50)


def test_free_model_never_trips_cost_cap() -> None:
    budget = TokenBudget(max_cost_usd=0.01, model="gpt-oss-120b")
    budget.check_before()
    budget.charge(input_tokens=5_000_000, output_tokens=5_000_000)  # free → no raise
    assert budget.used_cost_usd == 0.0


def test_paid_model_still_trips_cost_cap() -> None:
    budget = TokenBudget(max_cost_usd=0.01, model="claude-sonnet-4-6")
    with pytest.raises(BudgetExceededError):
        budget.charge(input_tokens=1_000_000, output_tokens=1_000_000)  # $18 > $0.01


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
