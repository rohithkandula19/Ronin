"""Stripe read-only MCP server reference.

Wraps a small set of Stripe REST endpoints behind safe, agent-friendly tools.
Read-only by design — there is no path to charge a card or update a subscription
through this module. Adding write ops should require an explicit subclass with
``ApprovalGate`` from ``ronin_hardening``.

Auth: ``STRIPE_API_KEY`` env var or pass ``api_key`` directly. Use a *restricted*
key (Stripe's "Restricted Keys" feature) scoped to read-only resources.

Tools shipped:
- ``list_customers(limit, email)``
- ``retrieve_customer(customer_id)``
- ``list_subscriptions(customer_id, status, limit)``
- ``list_charges(customer_id, limit)``
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field


STRIPE_BASE = "https://api.stripe.com/v1"


class StripeReadOnlyTools(BaseModel):
    """Thin httpx wrapper around Stripe's read-only REST endpoints.

    Pass a custom ``http`` client for testing or to set retry/timeout policies.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    api_key: str = Field(default_factory=lambda: os.environ.get("STRIPE_API_KEY", ""))
    http: Any = None
    base_url: str = STRIPE_BASE
    max_limit: int = 100

    def _client(self) -> httpx.Client:
        if self.http is not None:
            return self.http
        if not self.api_key:
            raise RuntimeError("STRIPE_API_KEY not set; pass api_key= or set the env var")
        return httpx.Client(
            base_url=self.base_url,
            auth=(self.api_key, ""),
            timeout=30,
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self._client()
        response = client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def _clamp_limit(self, limit: int) -> int:
        return max(1, min(self.max_limit, int(limit)))

    def list_customers(self, limit: int = 10, email: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": self._clamp_limit(limit)}
        if email:
            params["email"] = email
        return self._get("/customers", params).get("data", [])

    def retrieve_customer(self, customer_id: str) -> dict[str, Any]:
        if not customer_id.startswith("cus_"):
            raise ValueError("expected a customer id starting with 'cus_'")
        return self._get(f"/customers/{customer_id}")

    def list_subscriptions(
        self,
        customer_id: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": self._clamp_limit(limit)}
        if customer_id:
            params["customer"] = customer_id
        if status:
            params["status"] = status
        return self._get("/subscriptions", params).get("data", [])

    def list_charges(
        self,
        customer_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": self._clamp_limit(limit)}
        if customer_id:
            params["customer"] = customer_id
        return self._get("/charges", params).get("data", [])


def stripe_tools(api_key: str | None = None) -> dict[str, Any]:
    """Build a name -> handler dict ready to register with any MCP / agent runtime."""
    backend = StripeReadOnlyTools(api_key=api_key) if api_key else StripeReadOnlyTools()
    return {
        "stripe_list_customers": backend.list_customers,
        "stripe_retrieve_customer": backend.retrieve_customer,
        "stripe_list_subscriptions": backend.list_subscriptions,
        "stripe_list_charges": backend.list_charges,
    }
