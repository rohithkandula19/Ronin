"""Billing exception hierarchy.

Every failure mode is explicit and fail-closed: we would rather raise than
silently allow access, silently price something as free, or silently move
money. All billing exceptions derive from :class:`BillingError` so callers can
catch the whole family at a boundary.
"""

from __future__ import annotations


class BillingError(Exception):
    """Base class for every billing failure."""


class UnknownPlan(BillingError):
    """Raised when a plan slug is not present in the catalog (fail-closed)."""


class UnknownEntitlement(BillingError):
    """Raised when a limit key is not declared by a plan (fail-closed)."""


class IllegalTransition(BillingError):
    """Raised when a subscription is asked to make an illegal state change."""


class UnknownMeterPrice(BillingError):
    """Raised when an overage meter has no price.

    Mirrors the inference gateway rule: an unknown price is NEVER rendered as
    free. If usage exceeds entitlement on a meter we cannot price, we refuse to
    produce an invoice rather than quietly waive the charge.
    """


class PaymentsDisabled(BillingError):
    """Raised by the disabled payment provider whenever money movement is attempted."""


__all__ = [
    "BillingError",
    "IllegalTransition",
    "PaymentsDisabled",
    "UnknownEntitlement",
    "UnknownMeterPrice",
    "UnknownPlan",
]
