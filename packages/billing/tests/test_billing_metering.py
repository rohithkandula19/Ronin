"""Tests for append-only, idempotent usage metering."""

from __future__ import annotations

from datetime import datetime

import pytest

from ronin_billing import UsageStore, period_of

NOW = datetime(2026, 7, 22, 12, 0, 0)


def test_period_of_is_year_month() -> None:
    assert period_of(datetime(2026, 3, 4)) == "2026-03"


def test_record_usage_appends() -> None:
    store = UsageStore()
    assert store.record_usage("org1", "requests", 5, now=NOW, idempotency_key="k1") is True
    assert len(store.events()) == 1


def test_record_usage_is_idempotent() -> None:
    store = UsageStore()
    store.record_usage("org1", "requests", 5, now=NOW, idempotency_key="k1")
    # replay same key -> no-op, returns False, no second event
    assert store.record_usage("org1", "requests", 5, now=NOW, idempotency_key="k1") is False
    assert len(store.events()) == 1


def test_total_aggregates_same_key_dimension() -> None:
    store = UsageStore()
    store.record_usage("org1", "requests", 5, now=NOW, idempotency_key="a")
    store.record_usage("org1", "requests", 7, now=NOW, idempotency_key="b")
    assert store.total("org1", "2026-07", "requests") == 12


def test_aggregate_groups_by_meter() -> None:
    store = UsageStore()
    store.record_usage("org1", "requests", 5, now=NOW, idempotency_key="a")
    store.record_usage("org1", "storage_bytes", 100, now=NOW, idempotency_key="b")
    assert store.aggregate("org1", "2026-07") == {"requests": 5, "storage_bytes": 100}


def test_aggregate_isolates_org_and_period() -> None:
    store = UsageStore()
    store.record_usage("org1", "requests", 5, now=NOW, idempotency_key="a")
    store.record_usage("org2", "requests", 9, now=NOW, idempotency_key="b")
    store.record_usage("org1", "requests", 3, now=datetime(2026, 8, 1), idempotency_key="c")
    assert store.aggregate("org1", "2026-07") == {"requests": 5}


def test_record_usage_rejects_nonpositive_quantity() -> None:
    store = UsageStore()
    with pytest.raises(ValueError):
        store.record_usage("org1", "requests", 0, now=NOW, idempotency_key="a")


def test_record_usage_requires_idempotency_key() -> None:
    store = UsageStore()
    with pytest.raises(ValueError):
        store.record_usage("org1", "requests", 1, now=NOW, idempotency_key="")


def test_events_are_frozen() -> None:
    store = UsageStore()
    store.record_usage("org1", "requests", 5, now=NOW, idempotency_key="a")
    event = store.events()[0]
    with pytest.raises(Exception):
        event.quantity = 99  # type: ignore[misc]
