"""Centralized demo dataset.

Used by ``csk init --demo`` so the offline experience is rich enough to make
``csk briefing`` produce a non-trivial report.

All timestamps are seconds-since-epoch relative to a fixed reference date
(`REFERENCE_NOW`) so the demo dataset stays deterministic across runs.
"""
from __future__ import annotations

REFERENCE_NOW = 1731331200  # 2026-05-11 UTC, the canonical "today" for the demo


def _days_ago(days: int) -> int:
    return REFERENCE_NOW - days * 86_400


# --- Customers (8) ---------------------------------------------------------

CUSTOMERS = [
    {"id": "cus_demo_alice", "email": "alice@acme.com", "name": "Alice Acme", "created": _days_ago(180)},
    {"id": "cus_demo_bob", "email": "bob@beta.io", "name": "Bob Beta", "created": _days_ago(140)},
    {"id": "cus_demo_carol", "email": "carol@charlie.dev", "name": "Carol Charlie", "created": _days_ago(95)},
    {"id": "cus_demo_dave", "email": "dave@delta.app", "name": "Dave Delta", "created": _days_ago(63)},
    {"id": "cus_demo_eve", "email": "eve@echo.co", "name": "Eve Echo", "created": _days_ago(28)},
    {"id": "cus_demo_frank", "email": "frank@foxtrot.dev", "name": "Frank Foxtrot", "created": _days_ago(14)},
    {"id": "cus_demo_grace", "email": "grace@golf.io", "name": "Grace Golf", "created": _days_ago(7)},
    {"id": "cus_demo_henry", "email": "henry@hotel.net", "name": "Henry Hotel", "created": _days_ago(3)},
]


# --- Subscriptions (15) ----------------------------------------------------
# amount is in cents, Stripe-style.

SUBSCRIPTIONS = [
    {"id": "sub_1", "customer": "cus_demo_alice", "status": "active", "amount": 4900, "plan": "Pro", "created": _days_ago(180)},
    {"id": "sub_2", "customer": "cus_demo_bob", "status": "active", "amount": 2900, "plan": "Starter", "created": _days_ago(140)},
    {"id": "sub_3", "customer": "cus_demo_carol", "status": "canceled", "amount": 4900, "plan": "Pro", "created": _days_ago(95), "canceled_at": _days_ago(3)},
    {"id": "sub_4", "customer": "cus_demo_dave", "status": "active", "amount": 9900, "plan": "Team", "created": _days_ago(63)},
    {"id": "sub_5", "customer": "cus_demo_eve", "status": "active", "amount": 2900, "plan": "Starter", "created": _days_ago(28)},
    {"id": "sub_6", "customer": "cus_demo_frank", "status": "past_due", "amount": 4900, "plan": "Pro", "created": _days_ago(14)},
    {"id": "sub_7", "customer": "cus_demo_grace", "status": "active", "amount": 9900, "plan": "Team", "created": _days_ago(7)},
    {"id": "sub_8", "customer": "cus_demo_henry", "status": "active", "amount": 2900, "plan": "Starter", "created": _days_ago(3)},
]


# --- Charges (30) -----------------------------------------------------------
# Spread over the last ~90 days.

CHARGES = [
    {"id": "ch_001", "customer": "cus_demo_alice", "amount": 4900, "status": "succeeded", "created": _days_ago(2)},
    {"id": "ch_002", "customer": "cus_demo_bob", "amount": 2900, "status": "succeeded", "created": _days_ago(2)},
    {"id": "ch_003", "customer": "cus_demo_dave", "amount": 9900, "status": "succeeded", "created": _days_ago(2)},
    {"id": "ch_004", "customer": "cus_demo_frank", "amount": 4900, "status": "failed", "created": _days_ago(2), "failure_message": "card_declined"},
    {"id": "ch_005", "customer": "cus_demo_grace", "amount": 9900, "status": "succeeded", "created": _days_ago(5)},
    {"id": "ch_006", "customer": "cus_demo_eve", "amount": 2900, "status": "succeeded", "created": _days_ago(6)},
    {"id": "ch_007", "customer": "cus_demo_carol", "amount": 4900, "status": "refunded", "created": _days_ago(8)},
    {"id": "ch_008", "customer": "cus_demo_alice", "amount": 4900, "status": "succeeded", "created": _days_ago(32)},
    {"id": "ch_009", "customer": "cus_demo_bob", "amount": 2900, "status": "succeeded", "created": _days_ago(32)},
    {"id": "ch_010", "customer": "cus_demo_dave", "amount": 9900, "status": "succeeded", "created": _days_ago(32)},
    {"id": "ch_011", "customer": "cus_demo_eve", "amount": 2900, "status": "succeeded", "created": _days_ago(35)},
    {"id": "ch_012", "customer": "cus_demo_henry", "amount": 2900, "status": "succeeded", "created": _days_ago(3)},
    {"id": "ch_013", "customer": "cus_demo_frank", "amount": 4900, "status": "failed", "created": _days_ago(9), "failure_message": "insufficient_funds"},
    {"id": "ch_014", "customer": "cus_demo_alice", "amount": 4900, "status": "succeeded", "created": _days_ago(62)},
    {"id": "ch_015", "customer": "cus_demo_bob", "amount": 2900, "status": "succeeded", "created": _days_ago(62)},
]


# --- Linear teams + issues -------------------------------------------------

TEAMS = [
    {"id": "t_eng", "key": "ENG", "name": "Engineering", "description": "Core platform"},
    {"id": "t_growth", "key": "GRW", "name": "Growth"},
    {"id": "t_design", "key": "DES", "name": "Design"},
]

ISSUES = [
    {"id": "i1", "identifier": "ENG-101", "title": "Stripe webhook flake — duplicate events", "state": {"name": "In Progress"}, "priority": 1, "team": {"key": "ENG"}, "assignee": {"name": "Alice"}, "updatedAt": "2026-05-09"},
    {"id": "i2", "identifier": "ENG-102", "title": "Rotate session signing keys quarterly", "state": {"name": "Todo"}, "priority": 2, "team": {"key": "ENG"}, "assignee": None, "updatedAt": "2026-05-07"},
    {"id": "i3", "identifier": "ENG-103", "title": "Backfill missing analytics rows for May", "state": {"name": "In Progress"}, "priority": 2, "team": {"key": "ENG"}, "assignee": {"name": "Bob"}, "updatedAt": "2026-05-10"},
    {"id": "i4", "identifier": "ENG-104", "title": "Auth bug: SSO logout doesn't clear tabs", "state": {"name": "In Review"}, "priority": 2, "team": {"key": "ENG"}, "assignee": {"name": "Alice"}, "updatedAt": "2026-05-08"},
    {"id": "i5", "identifier": "ENG-105", "title": "Postgres slow query on /reports", "state": {"name": "Done"}, "priority": 3, "team": {"key": "ENG"}, "assignee": {"name": "Bob"}, "updatedAt": "2026-05-06"},
    {"id": "i6", "identifier": "GRW-12", "title": "A/B test new pricing page copy", "state": {"name": "In Progress"}, "priority": 2, "team": {"key": "GRW"}, "assignee": {"name": "Eve"}, "updatedAt": "2026-05-10"},
    {"id": "i7", "identifier": "GRW-13", "title": "Onboarding email sequence drop-off after step 3", "state": {"name": "Todo"}, "priority": 3, "team": {"key": "GRW"}, "assignee": None, "updatedAt": "2026-05-04"},
    {"id": "i8", "identifier": "DES-7", "title": "Onboarding redesign — small-screen pass", "state": {"name": "In Review"}, "priority": 3, "team": {"key": "DES"}, "assignee": {"name": "Frank"}, "updatedAt": "2026-05-09"},
    {"id": "i9", "identifier": "DES-8", "title": "Empty-state illustrations for /dashboard", "state": {"name": "Todo"}, "priority": 4, "team": {"key": "DES"}, "assignee": None, "updatedAt": "2026-05-03"},
    {"id": "i10", "identifier": "ENG-106", "title": "Customer-data export tool for support", "state": {"name": "Todo"}, "priority": 3, "team": {"key": "ENG"}, "assignee": None, "updatedAt": "2026-05-02"},
    {"id": "i11", "identifier": "ENG-107", "title": "Rate-limit /api/agent runs per tenant", "state": {"name": "Todo"}, "priority": 1, "team": {"key": "ENG"}, "assignee": None, "updatedAt": "2026-05-10"},
    {"id": "i12", "identifier": "GRW-14", "title": "Win-back email for canceled subs", "state": {"name": "Todo"}, "priority": 2, "team": {"key": "GRW"}, "assignee": None, "updatedAt": "2026-05-11"},
]
