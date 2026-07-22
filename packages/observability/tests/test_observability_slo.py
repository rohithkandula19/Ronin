"""SLO: success ratio, objective, error budget, burn rate, fail-closed."""

from __future__ import annotations

import pytest

from ronin_observability import SLO, SLOStatus


def test_objective_must_be_open_unit_interval():
    with pytest.raises(ValueError):
        SLO(name="x", objective=1.0)
    with pytest.raises(ValueError):
        SLO(name="x", objective=0.0)


def test_zero_traffic_is_unknown_not_healthy():
    slo = SLO(name="api", objective=0.99)
    r = slo.evaluate(good=0, total=0)
    assert r.status is SLOStatus.unknown
    assert r.met is False
    assert r.success_ratio is None
    assert r.burn_rate is None


def test_met_objective_is_healthy():
    slo = SLO(name="api", objective=0.99)
    r = slo.evaluate(good=995, total=1000)
    assert r.success_ratio == 0.995
    assert r.met is True
    assert r.status is SLOStatus.healthy


def test_missed_objective_is_breaching():
    slo = SLO(name="api", objective=0.99)
    r = slo.evaluate(good=980, total=1000)
    assert r.met is False
    assert r.status is SLOStatus.breaching


def test_error_budget_absolute_and_pct():
    # objective 0.99 over 1000 -> 10 allowed failures.
    slo = SLO(name="api", objective=0.99)
    r = slo.evaluate(good=995, total=1000)
    assert r.error_budget_allowed == pytest.approx(10.0)
    # observed failures = 5, remaining = 5
    assert r.error_budget_remaining == pytest.approx(5.0)
    assert r.error_budget_remaining_pct == pytest.approx(50.0)


def test_burn_rate_one_at_sustainable_pace():
    # objective 0.99 -> allowed failure ratio 0.01. Observe exactly 1% failures.
    slo = SLO(name="api", objective=0.99)
    r = slo.evaluate(good=990, total=1000)
    assert r.burn_rate == pytest.approx(1.0)
    assert r.error_budget_remaining == pytest.approx(0.0)
    assert r.error_budget_remaining_pct == pytest.approx(0.0)


def test_burn_rate_above_one_when_overspending():
    slo = SLO(name="api", objective=0.99)
    r = slo.evaluate(good=970, total=1000)  # 3% failure vs 1% budget
    assert r.burn_rate == pytest.approx(3.0)
    assert r.error_budget_remaining < 0
    assert r.error_budget_remaining_pct < 0


def test_perfect_traffic_has_full_budget():
    slo = SLO(name="api", objective=0.999)
    r = slo.evaluate(good=1000, total=1000)
    assert r.met is True
    assert r.burn_rate == pytest.approx(0.0)
    assert r.error_budget_remaining_pct == pytest.approx(100.0)


def test_invalid_counts_rejected():
    slo = SLO(name="api", objective=0.99)
    with pytest.raises(ValueError):
        slo.evaluate(good=-1, total=10)
    with pytest.raises(ValueError):
        slo.evaluate(good=11, total=10)


def test_window_label_preserved():
    slo = SLO(name="api", objective=0.99, window="30d")
    r = slo.evaluate(good=1, total=1)
    assert r.window == "30d"
