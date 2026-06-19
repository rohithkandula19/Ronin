"""Tests for the universal action engine — ``ronin do "<task>"`` (act.py).

All offline. No network: the pure functions (``classify_task``, ``plan_steps``,
``stops_before_payment``) do no I/O, and ``run`` is exercised only through its
grounding gate with an injected/stubbed research path — research is never
actually executed here.

Core safety assertions:
- ``classify_task`` routes real-world tasks into the right vertical, and
  unknown tasks fall through to "generic".
- ``plan_steps`` produces an ordered plan whose LAST step is a payment
  (``kind="payment"``, ``reversible=False``, ``external=True``) for EVERY
  vertical, with all preceding steps reversible.
- ``stops_before_payment`` is True for every generated plan, and is False when a
  step is (mis)placed after a payment.
"""
from __future__ import annotations

import pytest

from ronin_cli.act import (
    KIND_PAYMENT,
    VERTICALS,
    classify_task,
    is_payment_step,
    payment_step,
    plan_steps,
    stops_before_payment,
)


# --------------------------------------------------------------------------- #
# classify_task                                                               #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "text, expected",
    [
        ("order me a pizza", "food"),
        ("get me sushi delivery tonight", "food"),
        ("book a flight from SFO to JFK on Friday", "travel"),
        ("book a hotel in Paris", "travel"),
        ("make a reservation at Nobu for two", "reservation"),
        ("reserve a table for 4 at 7pm", "reservation"),
        ("buy me a new keyboard on amazon", "shopping"),
        ("sign up for a Spotify account", "signup"),
        ("register for the marathon", "signup"),
        ("water my plants", "generic"),
        ("", "generic"),
        ("   ", "generic"),
    ],
)
def test_classify_task_routes_to_vertical(text: str, expected: str) -> None:
    assert classify_task(text) == expected


def test_classify_task_always_returns_a_known_vertical() -> None:
    for text in ("anything at all", "do something weird", "asdf qwerty", ""):
        assert classify_task(text) in VERTICALS


# --------------------------------------------------------------------------- #
# plan_steps — the last step is always a blocked payment                      #
# --------------------------------------------------------------------------- #

def test_plan_last_step_is_a_blocked_payment_for_food() -> None:
    steps = plan_steps("order me a pizza", "food")
    assert len(steps) >= 2
    last = steps[-1]
    assert last["kind"] == KIND_PAYMENT == "payment"
    # The hard rule: the money-moving step can never auto-run.
    assert last["reversible"] is False
    assert last["external"] is True


@pytest.mark.parametrize("vertical", list(VERTICALS))
def test_plan_last_step_is_payment_for_every_vertical(vertical: str) -> None:
    steps = plan_steps("do the thing", vertical)
    last = steps[-1]
    assert last["kind"] == "payment"
    assert last["reversible"] is False
    assert last["external"] is True
    # There is exactly one payment step, and it is the terminal one.
    payments = [i for i, s in enumerate(steps) if is_payment_step(s)]
    assert payments == [len(steps) - 1]


@pytest.mark.parametrize("vertical", list(VERTICALS))
def test_all_steps_before_payment_are_reversible(vertical: str) -> None:
    steps = plan_steps("do the thing", vertical)
    for step in steps[:-1]:
        assert step["reversible"] is True, step
        assert step["kind"] != "payment"


@pytest.mark.parametrize("vertical", list(VERTICALS))
def test_plan_steps_have_the_action_contract_shape(vertical: str) -> None:
    # Every step must be a valid Action dict (the shape approvals expects).
    required = {"kind", "summary", "reversible", "external", "cost", "target", "details"}
    for step in plan_steps("order me a pizza", vertical):
        assert required <= set(step.keys())
        assert isinstance(step["reversible"], bool)
        assert isinstance(step["external"], bool)
        assert step["cost"] is None or isinstance(step["cost"], (int, float))


def test_unknown_vertical_falls_back_to_generic_plan() -> None:
    # An unrecognized vertical still yields a safe, payment-terminal plan.
    steps = plan_steps("do something", "not-a-real-vertical")
    assert steps[-1]["kind"] == "payment"
    assert stops_before_payment(steps)


def test_payment_step_factory_is_never_reversible_and_has_no_invented_cost() -> None:
    p = payment_step("Place the order and pay")
    assert p["kind"] == "payment"
    assert p["reversible"] is False and p["external"] is True
    # cost stays None unless a grounded price is passed (we never invent one).
    assert p["cost"] is None


# --------------------------------------------------------------------------- #
# stops_before_payment — nothing executes after a payment                      #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("vertical", list(VERTICALS))
def test_generated_plans_stop_before_payment(vertical: str) -> None:
    assert stops_before_payment(plan_steps("a task", vertical)) is True


def test_stops_before_payment_empty_and_no_payment_are_safe() -> None:
    assert stops_before_payment([]) is True
    safe = [
        {"kind": "research", "summary": "r", "reversible": True, "external": False,
         "cost": None, "target": "", "details": ""},
        {"kind": "choose", "summary": "c", "reversible": True, "external": False,
         "cost": None, "target": "", "details": ""},
    ]
    assert stops_before_payment(safe) is True


def test_stops_before_payment_false_when_a_step_follows_payment() -> None:
    # A payment followed by ANY further step is unsafe — the guard must catch it.
    bad = [
        {"kind": "research", "summary": "r", "reversible": True, "external": False,
         "cost": None, "target": "", "details": ""},
        payment_step("pay now"),
        {"kind": "prepare", "summary": "do more after paying", "reversible": True,
         "external": True, "cost": None, "target": "browser", "details": ""},
    ]
    assert is_payment_step(bad[1]) is True
    assert stops_before_payment(bad) is False


def test_is_payment_step_detects_kind_and_money_moving_shape() -> None:
    assert is_payment_step(payment_step("pay")) is True
    # Irreversible + external + a concrete cost also reads as money-moving.
    assert is_payment_step(
        {"kind": "prepare", "summary": "charge $5", "reversible": False,
         "external": True, "cost": 5.0, "target": "", "details": ""}
    ) is True
    # A plain reversible local step is not a payment.
    assert is_payment_step(
        {"kind": "research", "summary": "look up", "reversible": True,
         "external": False, "cost": None, "target": "", "details": ""}
    ) is False
    assert is_payment_step("not a dict") is False
