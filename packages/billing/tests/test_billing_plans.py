"""Tests for plans, the catalog, and the fail-closed entitlements checker."""

from __future__ import annotations

import pytest

from ronin_billing import (
    UNLIMITED,
    Entitlements,
    Plan,
    PlanCatalog,
    UnknownEntitlement,
    UnknownPlan,
)


def _pro() -> Plan:
    return Plan(
        slug="pro",
        display="Pro",
        monthly_price_cents=4900,
        limits={"max_seats": 10, "monthly_request_quota": 100_000, "storage_bytes": UNLIMITED},
        features=frozenset({"sso", "priority_support"}),
    )


def test_catalog_get_and_slugs() -> None:
    cat = PlanCatalog([_pro()])
    assert cat.get("pro").display == "Pro"
    assert cat.slugs() == ["pro"]


def test_catalog_rejects_duplicate_slug() -> None:
    cat = PlanCatalog([_pro()])
    with pytest.raises(ValueError):
        cat.add(_pro())


def test_entitlements_allows_known_feature() -> None:
    ent = Entitlements(PlanCatalog([_pro()]))
    assert ent.allows("pro", "sso") is True


def test_entitlements_denies_ungranted_feature() -> None:
    ent = Entitlements(PlanCatalog([_pro()]))
    assert ent.allows("pro", "audit_log") is False


def test_within_limit_true_at_boundary() -> None:
    ent = Entitlements(PlanCatalog([_pro()]))
    assert ent.within_limit("pro", "max_seats", 10) is True


def test_within_limit_false_over_boundary() -> None:
    ent = Entitlements(PlanCatalog([_pro()]))
    assert ent.within_limit("pro", "max_seats", 11) is False


def test_within_limit_unlimited_always_true() -> None:
    ent = Entitlements(PlanCatalog([_pro()]))
    assert ent.within_limit("pro", "storage_bytes", 10**18) is True


def test_unknown_plan_fails_closed() -> None:
    ent = Entitlements(PlanCatalog([_pro()]))
    with pytest.raises(UnknownPlan):
        ent.allows("enterprise", "sso")


def test_unknown_limit_key_fails_closed() -> None:
    ent = Entitlements(PlanCatalog([_pro()]))
    with pytest.raises(UnknownEntitlement):
        ent.within_limit("pro", "api_calls", 1)


def test_entitlements_accepts_plan_object_directly() -> None:
    ent = Entitlements()
    assert ent.allows(_pro(), "priority_support") is True


def test_plan_is_frozen() -> None:
    plan = _pro()
    with pytest.raises(Exception):
        plan.monthly_price_cents = 0  # type: ignore[misc]


def test_plan_rejects_negative_limit() -> None:
    with pytest.raises(Exception):
        Plan(slug="x", display="X", monthly_price_cents=0, limits={"seats": -5})
