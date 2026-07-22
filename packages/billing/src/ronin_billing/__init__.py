"""Ronin billing — plans, entitlements, metering, subscriptions, invoices.

This package handles the *accounting* side of billing entirely locally and
deterministically. Design guarantees, each tested:

- **No live payments, ever.** The only shipped :class:`PaymentProvider` is
  :class:`DisabledPaymentProvider`, whose money-moving methods raise
  :class:`PaymentsDisabled`. Enabling real payments would need real credentials
  and explicit authorization (BLOCKED_CREDENTIALS); this package never wires a
  network payment provider.
- **Fail-closed entitlements.** Unknown plan or unknown limit key raises rather
  than defaulting to "allow".
- **No double counting.** Usage metering is append-only and idempotent on a
  caller-supplied idempotency key.
- **Legal transitions only.** The subscription state machine refuses illegal
  moves; ``canceled`` is terminal.
- **Honest pricing.** Overage on a meter with no price raises rather than being
  silently free — mirroring the inference gateway's "unknown price is never
  free" rule.
- **Deterministic & money-safe.** Callers supply ``now``; money is integer
  minor units (cents) — no float arithmetic on currency.
"""

from __future__ import annotations

from .errors import (
    BillingError,
    IllegalTransition,
    PaymentsDisabled,
    UnknownEntitlement,
    UnknownMeterPrice,
    UnknownPlan,
)
from .invoices import Invoice, LineItem, PriceTable, compute_invoice
from .metering import UsageEvent, UsageStore, period_of
from .payments import (
    BLOCKED_CREDENTIALS,
    DisabledPaymentProvider,
    PaymentProvider,
    attempt_settlement,
)
from .plans import UNLIMITED, Entitlements, Plan, PlanCatalog
from .subscriptions import Subscription, SubscriptionState, can_transition

__all__ = [
    "BLOCKED_CREDENTIALS",
    "UNLIMITED",
    "BillingError",
    "DisabledPaymentProvider",
    "Entitlements",
    "IllegalTransition",
    "Invoice",
    "LineItem",
    "PaymentProvider",
    "PaymentsDisabled",
    "Plan",
    "PlanCatalog",
    "PriceTable",
    "Subscription",
    "SubscriptionState",
    "UnknownEntitlement",
    "UnknownMeterPrice",
    "UnknownPlan",
    "UsageEvent",
    "UsageStore",
    "attempt_settlement",
    "can_transition",
    "compute_invoice",
    "period_of",
]
