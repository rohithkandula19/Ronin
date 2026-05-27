"""Stripe billing — Checkout sessions + webhook handlers.

This is a scaffold. To go live:

1. Stripe dashboard → Products → create "csk Pro" ($19/mo) and "csk Team" ($99/mo).
   Copy the price IDs into ``STRIPE_PRICE_PRO`` / ``STRIPE_PRICE_TEAM`` env vars.
2. Dashboard → Developers → API keys → copy the secret into ``STRIPE_SECRET_KEY``.
3. Dashboard → Developers → Webhooks → "Add endpoint" → ``https://<your-host>/webhooks/stripe``.
   Copy the signing secret into ``STRIPE_WEBHOOK_SECRET``.

Endpoints (wired in main.py):
- ``POST /billing/checkout``    — create a Stripe Checkout session for the calling user
- ``POST /webhooks/stripe``     — Stripe → us; updates User.plan on subscription events
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session

from .db import Plan, User


STRIPE_API_BASE = "https://api.stripe.com/v1"


@dataclass(frozen=True)
class BillingConfig:
    secret_key: str
    webhook_secret: str
    price_pro: str
    price_team: str
    success_url: str
    cancel_url: str

    @classmethod
    def from_env(cls) -> "BillingConfig":
        return cls(
            secret_key=os.environ.get("STRIPE_SECRET_KEY", ""),
            webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
            price_pro=os.environ.get("STRIPE_PRICE_PRO", ""),
            price_team=os.environ.get("STRIPE_PRICE_TEAM", ""),
            success_url=os.environ.get("STRIPE_SUCCESS_URL", "http://localhost:3000/dashboard?billing=ok"),
            cancel_url=os.environ.get("STRIPE_CANCEL_URL", "http://localhost:3000/dashboard?billing=cancel"),
        )


PLAN_TO_PRICE_FIELD = {
    Plan.PRO: "price_pro",
    Plan.TEAM: "price_team",
}


def create_checkout_session(
    user: User,
    plan: Plan,
    config: BillingConfig | None = None,
    *,
    http: Any | None = None,
) -> str:
    """Returns a Stripe-hosted Checkout URL the user can be redirected to.

    The Checkout session is created in subscription mode so Stripe handles plan
    selection, card capture, and recurring billing. On success Stripe redirects
    back to ``success_url`` (settable per-env).
    """
    cfg = config or BillingConfig.from_env()
    if not cfg.secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY not configured")
    if plan == Plan.FREE:
        raise ValueError("free plan doesn't need checkout")

    field = PLAN_TO_PRICE_FIELD[plan]
    price_id = getattr(cfg, field)
    if not price_id:
        raise RuntimeError(f"price for {plan.value!r} not configured (set the matching env var)")

    body = {
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": 1,
        "customer_email": user.email,
        "client_reference_id": str(user.id),
        "success_url": cfg.success_url,
        "cancel_url": cfg.cancel_url,
        "allow_promotion_codes": "true",
    }
    client = http or httpx.Client(timeout=30)
    resp = client.post(
        f"{STRIPE_API_BASE}/checkout/sessions",
        data=body,
        auth=(cfg.secret_key, ""),
    )
    resp.raise_for_status()
    payload = resp.json()
    url = payload.get("url")
    if not url:
        raise RuntimeError(f"stripe didn't return a checkout url: {payload!r}")
    return url


def create_portal_session(
    user: User,
    *,
    return_url: str | None = None,
    config: BillingConfig | None = None,
    http: Any | None = None,
) -> str:
    """Open the Stripe Customer Portal so the user can change/cancel their plan.

    Requires that the user has already gone through Checkout once — Stripe needs
    a Customer ID. We store that in ``User.stripe_customer_id`` on the
    ``checkout.session.completed`` webhook.
    """
    cfg = config or BillingConfig.from_env()
    if not cfg.secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY not configured")
    if not user.stripe_customer_id:
        raise RuntimeError(
            "user has no Stripe customer record yet — they need to complete Checkout first"
        )

    body = {
        "customer": user.stripe_customer_id,
        "return_url": return_url or os.environ.get("STRIPE_PORTAL_RETURN_URL", "http://localhost:3000/dashboard"),
    }
    client = http or httpx.Client(timeout=30)
    resp = client.post(
        f"{STRIPE_API_BASE}/billing_portal/sessions",
        data=body,
        auth=(cfg.secret_key, ""),
    )
    resp.raise_for_status()
    url = resp.json().get("url")
    if not url:
        raise RuntimeError("stripe didn't return a portal url")
    return url


# ---------- webhook signature ----------

def verify_stripe_signature(
    payload: bytes,
    signature_header: str,
    webhook_secret: str,
    *,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify a Stripe webhook signature using their docs'd HMAC scheme.

    Stripe's Stripe-Signature header is comma-separated:
        t=<timestamp>,v1=<sig1>,v1=<sig2>,...
    We compare HMAC-SHA256(secret, "<t>.<payload>") against any v1.
    """
    if not signature_header or not webhook_secret:
        return False
    parts: dict[str, list[str]] = {}
    for chunk in signature_header.split(","):
        if "=" not in chunk:
            continue
        k, _, v = chunk.partition("=")
        parts.setdefault(k.strip(), []).append(v.strip())

    timestamps = parts.get("t") or []
    signatures = parts.get("v1") or []
    if not timestamps or not signatures:
        return False

    try:
        ts = int(timestamps[0])
    except ValueError:
        return False
    if abs(time.time() - ts) > tolerance_seconds:
        return False

    signed_payload = f"{ts}.{payload.decode('utf-8', errors='replace')}".encode("utf-8")
    expected = hmac.new(webhook_secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


# ---------- webhook event handling ----------

def apply_webhook_event(session: Session, event: dict[str, Any]) -> str:
    """Mutates User.plan based on event type. Returns a short message for logging.

    Handled events:
    - ``checkout.session.completed``         — user finished checkout → upgrade plan
    - ``customer.subscription.deleted``      — subscription canceled → downgrade to FREE
    - ``customer.subscription.updated``      — plan-change handler (e.g. Pro→Team)
    """
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if etype == "checkout.session.completed":
        user_id = _user_id_from(obj.get("client_reference_id"))
        plan = _plan_from_checkout(obj)
        # Stash the Stripe customer ID so we can open Customer Portal later.
        customer_id = obj.get("customer")
        if user_id is not None and customer_id:
            user = session.get(User, user_id)
            if user is not None and isinstance(customer_id, str):
                user.stripe_customer_id = customer_id
                session.flush()
        return _set_user_plan(session, user_id, plan)

    if etype == "customer.subscription.updated":
        user_id = _user_id_from(obj.get("metadata", {}).get("user_id"))
        plan = _plan_from_subscription(obj)
        return _set_user_plan(session, user_id, plan)

    if etype == "customer.subscription.deleted":
        user_id = _user_id_from(obj.get("metadata", {}).get("user_id"))
        return _set_user_plan(session, user_id, Plan.FREE)

    return f"ignored event type {etype!r}"


def _user_id_from(raw: Any) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _plan_from_checkout(obj: dict[str, Any]) -> Plan:
    """Look at the line items / amount_total / metadata to figure out the plan."""
    amount = obj.get("amount_total") or 0
    # 1900¢ = Pro $19, 9900¢ = Team $99 — adjust if you change prices
    if amount >= 9000:
        return Plan.TEAM
    if amount >= 1500:
        return Plan.PRO
    return Plan.FREE


def _plan_from_subscription(obj: dict[str, Any]) -> Plan:
    """Subscription objects carry items[].price.unit_amount in cents."""
    items = (obj.get("items") or {}).get("data") or []
    amount = 0
    for item in items:
        amt = ((item.get("price") or {}).get("unit_amount")) or 0
        amount = max(amount, amt)
    if amount >= 9000:
        return Plan.TEAM
    if amount >= 1500:
        return Plan.PRO
    return Plan.FREE


def _set_user_plan(session: Session, user_id: int | None, plan: Plan) -> str:
    if user_id is None:
        return "no user_id on event — skipped"
    user = session.get(User, user_id)
    if user is None:
        return f"user_id={user_id} not found"
    prior = user.plan
    user.plan = plan
    session.flush()
    return f"user_id={user_id} {prior.value} → {plan.value}"
