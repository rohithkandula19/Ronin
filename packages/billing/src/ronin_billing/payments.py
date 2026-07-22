"""Payment provider interface and the ONLY shipped implementation: disabled.

LIVE PAYMENTS ARE DISABLED BY DESIGN. This package computes invoices locally
but never moves money. The :class:`PaymentProvider` protocol defines the seam
where a real provider would attach; the only concrete implementation shipped
here is :class:`DisabledPaymentProvider`, whose every money-moving method raises
:class:`PaymentsDisabled`.

Turning payments on would require real provider credentials AND explicit human
authorization — both of which are intentionally absent. That deliberate refusal
is surfaced to callers via the ``BLOCKED_CREDENTIALS`` reason code.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .errors import PaymentsDisabled
from .invoices import Invoice

#: Reason code returned/raised wherever a money-movement attempt is refused.
BLOCKED_CREDENTIALS = "BLOCKED_CREDENTIALS"

_DISABLED_MESSAGE = (
    "live payments are disabled by design: enabling them requires real payment "
    "provider credentials AND explicit authorization, both intentionally absent "
    f"({BLOCKED_CREDENTIALS})"
)


@runtime_checkable
class PaymentProvider(Protocol):
    """Interface a real payment backend would implement.

    Both methods move (or authorize the movement of) money, so the disabled
    implementation refuses them. Amounts are integer minor units (cents).
    """

    def charge(self, *, amount_cents: int, currency: str, reference: str) -> object:
        """Charge a computed amount against a payment method. Money movement."""
        ...

    def checkout(self, *, invoice: Invoice, success_url: str, cancel_url: str) -> object:
        """Begin a hosted checkout for an invoice. Money movement."""
        ...


class DisabledPaymentProvider:
    """The enforcement point that keeps money off. Every method fails closed."""

    reason = BLOCKED_CREDENTIALS

    def charge(self, *, amount_cents: int, currency: str, reference: str) -> object:
        raise PaymentsDisabled(_DISABLED_MESSAGE)

    def checkout(self, *, invoice: Invoice, success_url: str, cancel_url: str) -> object:
        raise PaymentsDisabled(_DISABLED_MESSAGE)


def attempt_settlement(invoice: Invoice, provider: PaymentProvider) -> Invoice:
    """Attempt to settle an invoice through a provider.

    With the shipped :class:`DisabledPaymentProvider` this always raises
    :class:`PaymentsDisabled`, so the invoice stays ``paid=False`` /
    ``settled=False``. This is the single choke point money would pass through.
    """
    provider.charge(
        amount_cents=invoice.total_cents,
        currency=invoice.currency,
        reference=f"{invoice.org_id}:{invoice.period}",
    )
    # Unreachable with the disabled provider; a real provider would return a
    # settled copy here. Kept explicit so the enforcement seam is obvious.
    return invoice.model_copy(update={"paid": True, "settled": True, "status": "paid"})


__all__ = [
    "BLOCKED_CREDENTIALS",
    "DisabledPaymentProvider",
    "PaymentProvider",
    "attempt_settlement",
]
