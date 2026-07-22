"""Compute-only invoice generation. This module NEVER moves money.

Given a plan, the metered usage for a period, and an overage price table, we
produce an :class:`Invoice` with a base line item and one overage line item per
meter that exceeded its included allowance. Every invoice is stamped
``status="computed"``, ``paid=False`` and ``settled=False`` — it is an
arithmetic artifact, not a payment.

Honest pricing: if a meter has overage but no price in the table we raise
:class:`UnknownMeterPrice` rather than silently treating the overage as free.
All amounts are integer minor units (cents); no float arithmetic touches money.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .errors import UnknownMeterPrice
from .plans import UNLIMITED, Plan


class PriceTable(BaseModel):
    """Overage prices in integer cents-per-unit, keyed by meter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = "USD"
    overage_cents_per_unit: dict[str, int] = Field(default_factory=dict)

    def price_for(self, meter: str) -> int:
        """Cents-per-unit for ``meter`` or raise :class:`UnknownMeterPrice`."""
        try:
            price = self.overage_cents_per_unit[meter]
        except KeyError:
            raise UnknownMeterPrice(
                f"no overage price for meter {meter!r}; refusing to price it as free"
            ) from None
        if price < 0:
            raise ValueError(f"overage price for {meter!r} must be >= 0")
        return price


class LineItem(BaseModel):
    """One invoice line. ``amount_cents == quantity * unit_price_cents``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str  # "base" | "overage"
    description: str
    meter: str | None = None
    quantity: int = Field(ge=0)
    unit_price_cents: int = Field(ge=0)
    amount_cents: int = Field(ge=0)


class Invoice(BaseModel):
    """A computed, unsettled invoice. Never represents a completed payment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: str
    plan_slug: str
    period: str
    currency: str = "USD"
    line_items: tuple[LineItem, ...] = ()
    total_cents: int = Field(ge=0)
    status: str = "computed"
    paid: bool = False
    settled: bool = False
    computed_at: str = ""


def compute_invoice(
    *,
    org_id: str,
    plan: Plan,
    usage: dict[str, int],
    price_table: PriceTable,
    now: datetime,
    period: str,
) -> Invoice:
    """Compute an invoice for one org/period. Pure arithmetic, no side effects.

    Line items: the plan's base monthly fee, then one overage item per meter
    whose usage exceeds its included allowance (``plan.limits[meter]``). A meter
    with no declared allowance treats all usage as overage; a meter marked
    ``UNLIMITED`` never overages. Any overage on an unpriced meter raises
    :class:`UnknownMeterPrice`.
    """
    items: list[LineItem] = []

    base = LineItem(
        kind="base",
        description=f"{plan.display} base plan fee",
        meter=None,
        quantity=1,
        unit_price_cents=plan.monthly_price_cents,
        amount_cents=plan.monthly_price_cents,
    )
    items.append(base)
    total = plan.monthly_price_cents

    for meter in sorted(usage):  # sorted => deterministic line ordering
        used = usage[meter]
        if used <= 0:
            continue
        included = plan.included_for(meter)
        if included == UNLIMITED:
            continue
        allowance = included if included is not None else 0
        overage = used - allowance
        if overage <= 0:
            continue
        unit = price_table.price_for(meter)  # raises on unknown meter
        amount = overage * unit
        items.append(
            LineItem(
                kind="overage",
                description=f"{meter} overage ({overage} over {allowance})",
                meter=meter,
                quantity=overage,
                unit_price_cents=unit,
                amount_cents=amount,
            )
        )
        total += amount

    return Invoice(
        org_id=org_id,
        plan_slug=plan.slug,
        period=period,
        currency=price_table.currency,
        line_items=tuple(items),
        total_cents=total,
        status="computed",
        paid=False,
        settled=False,
        computed_at=now.isoformat(),
    )


__all__ = ["Invoice", "LineItem", "PriceTable", "compute_invoice"]
