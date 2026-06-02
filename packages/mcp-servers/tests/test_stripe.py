from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from ronin_mcp_servers import StripeReadOnlyTools


def _fake_response(payload: dict, status: int = 200) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    if status >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=response
        )
    return response


def test_list_customers_passes_clamped_limit() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.get.return_value = _fake_response({"data": [{"id": "cus_1"}]})
    stripe = StripeReadOnlyTools(api_key="sk_test_x", http=fake, max_limit=50)

    customers = stripe.list_customers(limit=999, email="alice@example.com")

    assert customers == [{"id": "cus_1"}]
    args, kwargs = fake.get.call_args
    assert args[0] == "/customers"
    assert kwargs["params"]["limit"] == 50  # clamped
    assert kwargs["params"]["email"] == "alice@example.com"


def test_retrieve_customer_validates_id_prefix() -> None:
    stripe = StripeReadOnlyTools(api_key="sk_test_x", http=MagicMock(spec=httpx.Client))
    with pytest.raises(ValueError, match="cus_"):
        stripe.retrieve_customer("not-a-stripe-id")


def test_list_subscriptions_filters_by_customer_and_status() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.get.return_value = _fake_response({"data": [{"id": "sub_1", "status": "active"}]})
    stripe = StripeReadOnlyTools(api_key="sk_test_x", http=fake)

    subs = stripe.list_subscriptions(customer_id="cus_123", status="active")
    assert subs and subs[0]["status"] == "active"

    _, kwargs = fake.get.call_args
    assert kwargs["params"]["customer"] == "cus_123"
    assert kwargs["params"]["status"] == "active"


def test_list_charges_with_customer_filter() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.get.return_value = _fake_response({"data": [{"id": "ch_1", "amount": 500}]})
    stripe = StripeReadOnlyTools(api_key="sk_test_x", http=fake)
    charges = stripe.list_charges(customer_id="cus_x", limit=5)
    assert charges == [{"id": "ch_1", "amount": 500}]


def test_propagates_api_errors() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.get.return_value = _fake_response({"error": "auth"}, status=401)
    stripe = StripeReadOnlyTools(api_key="sk_test_x", http=fake)
    with pytest.raises(httpx.HTTPStatusError):
        stripe.list_customers()


def test_factory_returns_named_handlers() -> None:
    from ronin_mcp_servers import stripe_tools

    handlers = stripe_tools(api_key="sk_test_x")
    assert "stripe_list_customers" in handlers
    assert "stripe_retrieve_customer" in handlers
    assert "stripe_list_subscriptions" in handlers
    assert "stripe_list_charges" in handlers
    assert all(callable(h) for h in handlers.values())
