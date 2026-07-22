"""Tests proving live payments are disabled and the seam refuses money movement."""

from __future__ import annotations

from datetime import datetime

import pytest

from ronin_billing import (
    BLOCKED_CREDENTIALS,
    DisabledPaymentProvider,
    PaymentProvider,
    PaymentsDisabled,
    Plan,
    PriceTable,
    attempt_settlement,
    compute_invoice,
)

NOW = datetime(2026, 7, 22, 12, 0, 0)


def _invoice():
    plan = Plan(slug="pro", display="Pro", monthly_price_cents=4900, limits={})
    return compute_invoice(
        org_id="org1",
        plan=plan,
        usage={},
        price_table=PriceTable(),
        now=NOW,
        period="2026-07",
    )


def test_charge_is_refused() -> None:
    provider = DisabledPaymentProvider()
    with pytest.raises(PaymentsDisabled):
        provider.charge(amount_cents=4900, currency="USD", reference="org1:2026-07")


def test_checkout_is_refused() -> None:
    provider = DisabledPaymentProvider()
    with pytest.raises(PaymentsDisabled):
        provider.checkout(invoice=_invoice(), success_url="s", cancel_url="c")


def test_refusal_message_cites_blocked_credentials() -> None:
    provider = DisabledPaymentProvider()
    with pytest.raises(PaymentsDisabled) as excinfo:
        provider.charge(amount_cents=1, currency="USD", reference="r")
    assert BLOCKED_CREDENTIALS in str(excinfo.value)
    assert provider.reason == BLOCKED_CREDENTIALS


def test_attempt_settlement_never_pays_with_disabled_provider() -> None:
    inv = _invoice()
    with pytest.raises(PaymentsDisabled):
        attempt_settlement(inv, DisabledPaymentProvider())
    # invoice remains unsettled
    assert inv.paid is False
    assert inv.settled is False


def test_disabled_provider_satisfies_protocol() -> None:
    assert isinstance(DisabledPaymentProvider(), PaymentProvider)
