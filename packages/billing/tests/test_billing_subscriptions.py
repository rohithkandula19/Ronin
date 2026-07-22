"""Tests for the subscription state machine and period window."""

from __future__ import annotations

from datetime import datetime

import pytest

from ronin_billing import (
    IllegalTransition,
    Subscription,
    SubscriptionState,
    can_transition,
)

NOW = datetime(2026, 7, 22, 12, 0, 0)


def _sub(state: SubscriptionState = SubscriptionState.TRIALING) -> Subscription:
    return Subscription(
        org_id="org1",
        plan_slug="pro",
        state=state,
        current_period_start="2026-07-01T00:00:00",
        current_period_end="2026-08-01T00:00:00",
        updated_at="2026-07-01T00:00:00",
    )


def test_trialing_to_active_is_legal() -> None:
    sub = _sub()
    sub.transition(SubscriptionState.ACTIVE, now=NOW)
    assert sub.state is SubscriptionState.ACTIVE
    assert sub.updated_at == NOW.isoformat()


def test_active_to_past_due_to_active() -> None:
    sub = _sub(SubscriptionState.ACTIVE)
    sub.transition(SubscriptionState.PAST_DUE, now=NOW)
    sub.transition(SubscriptionState.ACTIVE, now=NOW)
    assert sub.state is SubscriptionState.ACTIVE


def test_suspend_and_reactivate() -> None:
    sub = _sub(SubscriptionState.ACTIVE)
    sub.transition(SubscriptionState.SUSPENDED, now=NOW)
    sub.transition(SubscriptionState.ACTIVE, now=NOW)
    assert sub.state is SubscriptionState.ACTIVE


def test_canceled_is_terminal() -> None:
    sub = _sub(SubscriptionState.ACTIVE)
    sub.transition(SubscriptionState.CANCELED, now=NOW)
    with pytest.raises(IllegalTransition):
        sub.transition(SubscriptionState.ACTIVE, now=NOW)


def test_illegal_transition_raises_and_preserves_state() -> None:
    sub = _sub(SubscriptionState.TRIALING)
    with pytest.raises(IllegalTransition):
        sub.transition(SubscriptionState.PAST_DUE, now=NOW)
    assert sub.state is SubscriptionState.TRIALING


def test_can_transition_helper() -> None:
    assert can_transition(SubscriptionState.ACTIVE, SubscriptionState.SUSPENDED) is True
    assert can_transition(SubscriptionState.CANCELED, SubscriptionState.ACTIVE) is False


def test_is_period_active() -> None:
    sub = _sub()
    assert sub.is_period_active(datetime(2026, 7, 15)) is True
    assert sub.is_period_active(datetime(2026, 8, 2)) is False


def test_renew_period_moves_window() -> None:
    sub = _sub()
    sub.renew_period(start=datetime(2026, 8, 1), end=datetime(2026, 9, 1), now=NOW)
    assert sub.current_period_start == "2026-08-01T00:00:00"
    assert sub.updated_at == NOW.isoformat()


def test_renew_period_rejects_bad_window() -> None:
    sub = _sub()
    with pytest.raises(ValueError):
        sub.renew_period(start=datetime(2026, 9, 1), end=datetime(2026, 8, 1), now=NOW)
