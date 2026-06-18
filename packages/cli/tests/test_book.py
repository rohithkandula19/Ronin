"""Tests for `ronin book` - prepare-and-confirm bookings.

All offline. Search and browser are mocked. The core assertions:

- prepare_booking returns a structured, prepare-only result.
- No pay / submit / purchase path is ever taken (we record every action a
  fake browser is asked to do and assert none is a payment action).
- Missing browser extra falls back to search + manual steps.
- The payment guard blocks any final pay/submit action.
"""
from __future__ import annotations

import pytest

from ronin_cli.book import (
    FORBIDDEN_ACTIONS,
    PaymentBlocked,
    PrepareResult,
    assert_not_payment,
    is_payment_action,
    prepare_booking,
)

# A realistic web_search-shaped result block.
FAKE_SEARCH = (
    "Search results for 'flight SFO to JFK Friday':\n"
    "1. United UA123 nonstop $248\n"
    "   https://united.example/flights/ua123\n"
    "   Departs 8:00am, arrives 4:30pm, nonstop, 1 stop none\n"
    "2. Delta DL456 one stop\n"
    "   https://delta.example/flights/dl456\n"
    "   Departs 6:00am, $312, one stop in DEN\n"
)


def _fake_search(query, max_results):
    return FAKE_SEARCH


def test_returns_structured_prepare_only_result() -> None:
    r = prepare_booking("flight SFO to JFK Friday", search_fn=_fake_search)
    assert isinstance(r, PrepareResult)
    assert r.prepared_only is True
    assert r.paid is False
    # options parsed into structured form
    assert len(r.options) == 2
    first = r.options[0]
    assert "United UA123" in first.option
    assert first.price == "$248"
    assert first.link == "https://united.example/flights/ua123"
    assert first.details
    # next-step link is the first option's link
    assert r.next_step_link == "https://united.example/flights/ua123"
    # manual steps are always provided so the user can finish themselves
    assert r.manual_steps
    assert any("pay" in s.lower() for s in r.manual_steps)
    # as_dict is serializable and prepare-only
    d = r.as_dict()
    assert d["prepared_only"] is True and d["paid"] is False
    assert len(d["options"]) == 2


def test_offline_no_search_still_prepares() -> None:
    # No search_fn (offline): still a valid prepare-only result with manual steps.
    r = prepare_booking("dinner for two Friday 7pm")
    assert r.prepared_only is True and r.paid is False
    assert r.options == []
    assert r.manual_steps
    assert any("offline" in n.lower() or "not used" in n.lower() for n in r.notes)


def test_browser_extra_absent_falls_back_to_summary() -> None:
    # browser_prefill_fn=None models the browser extra not being installed.
    r = prepare_booking("flight SFO to JFK", search_fn=_fake_search,
                        browser_prefill_fn=None)
    assert r.browser_prefilled is False
    # we tell the user the manual steps and how to enable the browser extra
    assert any("browser extra not installed" in n for n in r.notes)
    assert r.manual_steps


def test_browser_prefill_never_takes_payment_action() -> None:
    # A fake browser that records every action it is asked to take, and refuses
    # (via the guard) to take a payment action. We assert no payment action was
    # ever performed.
    taken: list[str] = []

    def fake_prefill(request, result):
        # The pre-fill path may navigate and fill, but must guard every action.
        for action in ("navigate to booking page", "fill passenger name",
                       "fill contact email", "select seat"):
            assert_not_payment(action)   # raises if a payment action sneaks in
            taken.append(action)
        # It must STOP before payment. It does not even attempt to click pay.
        return True

    r = prepare_booking("flight SFO to JFK", search_fn=_fake_search,
                        browser_prefill_fn=fake_prefill)
    assert r.browser_prefilled is True
    assert r.paid is False and r.prepared_only is True
    # Hard assertion: no action taken was a pay/submit/purchase action.
    assert taken, "pre-fill should have taken at least one (non-payment) action"
    for action in taken:
        assert not is_payment_action(action), f"payment action taken: {action}"


def test_guard_blocks_payment_actions() -> None:
    for word in ("Pay now", "Complete booking", "Place order", "Checkout",
                 "Buy ticket", "Submit order", "Confirm and pay", "Book now"):
        assert is_payment_action(word), word
        with pytest.raises(PaymentBlocked):
            assert_not_payment(word)


def test_guard_allows_prepare_actions() -> None:
    for word in ("navigate to page", "read page", "fill name", "select seat",
                 "review options"):
        assert is_payment_action(word) is False, word
        assert_not_payment(word)  # does not raise


def test_a_prefill_that_tries_to_pay_is_blocked_and_never_pays() -> None:
    # If a misbehaving pre-fill function tries to take a payment action, the
    # guard raises, prepare_booking catches it, and the result is still
    # prepare-only and unpaid.
    def evil_prefill(request, result):
        assert_not_payment("click Pay and submit order")  # must raise
        result.paid = True  # unreachable; proves we never get here
        return True

    r = prepare_booking("flight", search_fn=_fake_search,
                        browser_prefill_fn=evil_prefill)
    assert r.paid is False and r.prepared_only is True
    assert any("blocked a payment action" in n for n in r.notes)


def test_no_forbidden_word_missing() -> None:
    # Sanity: the guard list covers the obvious money-moving verbs.
    for must in ("pay", "purchase", "checkout", "buy", "charge"):
        assert must in FORBIDDEN_ACTIONS


# ---- CLI-level: the `ronin book` command, mocked end to end ----

def test_cli_book_command_prepare_only(monkeypatch) -> None:
    from typer.testing import CliRunner

    from ronin_cli import main as main_mod

    # Mock the default search to avoid any network.
    monkeypatch.setattr(main_mod, "_book_default_search", lambda: _fake_search)
    runner = CliRunner()
    r = runner.invoke(main_mod.app, ["book", "flight SFO to JFK Friday", "--no-browser"])
    assert r.exit_code == 0, r.stdout
    # The command tells the user it prepared (not paid) and lists an option link.
    assert "Prepared (not paid)" in r.stdout
    assert "united.example" in r.stdout
    assert "never pays" in r.stdout.lower()


def test_cli_book_browser_path_never_clicks_pay(monkeypatch) -> None:
    # Drive the real `_book_browser_prefill` against a fake browser_tools module
    # and assert it only ever navigates/reads - it never calls any click/pay/
    # submit function. We record every browser function invoked.
    from ronin_cli import book as book_mod

    calls: list[str] = []

    def fake_navigate(url):
        calls.append(f"navigate:{url}")
        return "navigated"

    def fake_read(*a, **k):
        calls.append("read")
        return "page text"

    # If the code ever reached for a click/type/pay, these would record it.
    def fake_click(target):
        calls.append(f"click:{target}")
        return "clicked"

    import types

    fake_bt = types.SimpleNamespace(
        browse_navigate=fake_navigate,
        browse_read=fake_read,
        browse_click=fake_click,
        browser_tools_available=lambda: True,
    )
    monkeypatch.setitem(__import__("sys").modules, "ronin_cli.browser_tools", fake_bt)

    from ronin_cli.main import _book_browser_prefill

    result = PrepareResult(request="flight",
                           next_step_link="https://united.example/flights/ua123")
    prefilled = _book_browser_prefill("flight", result)
    assert prefilled is True
    # Only navigate + read were called; no click / pay / submit ever happened.
    assert calls == ["navigate:https://united.example/flights/ua123", "read"]
    assert not any(c.startswith("click") for c in calls)
    for c in calls:
        assert not book_mod.is_payment_action(c), c
