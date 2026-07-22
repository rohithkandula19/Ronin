"""Tests for compute-only invoice generation."""

from __future__ import annotations

from datetime import datetime

import pytest

from ronin_billing import (
    UNLIMITED,
    Plan,
    PriceTable,
    UnknownMeterPrice,
    compute_invoice,
)

NOW = datetime(2026, 7, 22, 12, 0, 0)


def _plan() -> Plan:
    return Plan(
        slug="pro",
        display="Pro",
        monthly_price_cents=4900,
        limits={"requests": 100_000, "storage_bytes": UNLIMITED},
        features=frozenset(),
    )


def _prices() -> PriceTable:
    return PriceTable(currency="USD", overage_cents_per_unit={"requests": 1})


def _invoice(usage: dict[str, int], plan: Plan | None = None, prices: PriceTable | None = None):
    return compute_invoice(
        org_id="org1",
        plan=plan or _plan(),
        usage=usage,
        price_table=prices or _prices(),
        now=NOW,
        period="2026-07",
    )


def test_invoice_is_computed_and_unsettled() -> None:
    inv = _invoice({})
    assert inv.status == "computed"
    assert inv.paid is False
    assert inv.settled is False


def test_base_only_invoice_total() -> None:
    inv = _invoice({"requests": 50_000})  # within allowance
    assert inv.total_cents == 4900
    assert len(inv.line_items) == 1
    assert inv.line_items[0].kind == "base"


def test_overage_line_item_and_total() -> None:
    inv = _invoice({"requests": 150_000})  # 50k over @ 1 cent
    assert inv.total_cents == 4900 + 50_000
    overage = [li for li in inv.line_items if li.kind == "overage"]
    assert len(overage) == 1
    assert overage[0].quantity == 50_000
    assert overage[0].amount_cents == 50_000


def test_unlimited_meter_never_overages() -> None:
    inv = _invoice({"storage_bytes": 10**15})
    assert inv.total_cents == 4900  # no overage line for unlimited meter


def test_unknown_meter_price_raises_not_free() -> None:
    with pytest.raises(UnknownMeterPrice):
        # 'seats' has no allowance (all overage) and no price
        _invoice({"seats": 3})


def test_meter_within_allowance_needs_no_price() -> None:
    # 'requests' within allowance; a missing price should not matter
    prices = PriceTable(overage_cents_per_unit={})
    inv = _invoice({"requests": 100_000}, prices=prices)
    assert inv.total_cents == 4900


def test_line_amount_equals_quantity_times_unit() -> None:
    prices = PriceTable(overage_cents_per_unit={"requests": 3})
    inv = _invoice({"requests": 100_010}, prices=prices)
    overage = [li for li in inv.line_items if li.kind == "overage"][0]
    assert overage.amount_cents == overage.quantity * overage.unit_price_cents == 30


def test_invoice_currency_from_price_table() -> None:
    prices = PriceTable(currency="EUR", overage_cents_per_unit={"requests": 1})
    inv = _invoice({}, prices=prices)
    assert inv.currency == "EUR"


def test_invoice_is_frozen() -> None:
    inv = _invoice({})
    with pytest.raises(Exception):
        inv.paid = True  # type: ignore[misc]


def test_deterministic_line_ordering() -> None:
    prices = PriceTable(overage_cents_per_unit={"a_meter": 1, "z_meter": 1})
    plan = Plan(slug="p", display="P", monthly_price_cents=0, limits={})
    inv = _invoice({"z_meter": 2, "a_meter": 1}, plan=plan, prices=prices)
    overage_meters = [li.meter for li in inv.line_items if li.kind == "overage"]
    assert overage_meters == ["a_meter", "z_meter"]
