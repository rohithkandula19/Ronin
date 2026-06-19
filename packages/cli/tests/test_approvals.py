"""Tests for the universal approval gate (`ronin_cli.approvals`).

All offline, no network, no real prompting (the one impure function is driven
with a fake console / monkeypatched input). The core guarantees under test:

- gate_level is a pure, conservative classifier:
    * reversible + internal + non-payment edit   -> "auto"
    * external browser action                    -> "confirm"
    * payment / money-moving                     -> "block"  (never auto)
    * clearly destructive (rm -rf, drop table)   -> "block"
    * malformed / sparse actions fail closed (never "auto").
- is_payment catches money-moving actions by kind, cost, or keyword.
- describe renders a clear ASCII one-liner with the right tags.
- approve: auto+auto_ok runs silently; confirm always asks; block can never be
  auto-approved and warns; default-deny on empty / EOF.
"""
from __future__ import annotations

import builtins

import pytest

from ronin_cli.approvals import (
    AUTO,
    BLOCK,
    CONFIRM,
    approve,
    describe,
    gate_level,
    is_destructive,
    is_payment,
    normalize,
)


# ---------------------------------------------------------------------------
# gate_level — the pure policy
# ---------------------------------------------------------------------------


def test_reversible_internal_edit_is_auto() -> None:
    a = {"kind": "edit", "summary": "edit app.py", "reversible": True, "external": False}
    assert gate_level(a) == AUTO


def test_external_browser_action_is_confirm() -> None:
    a = {"kind": "browser", "summary": "click Search on example.com",
         "reversible": True, "external": True}
    assert gate_level(a) == CONFIRM


def test_irreversible_internal_action_is_confirm() -> None:
    # Not external, but not reversible either -> confirm (escalated over auto).
    a = {"kind": "shell", "summary": "run build", "reversible": False, "external": False}
    assert gate_level(a) == CONFIRM


def test_payment_is_block() -> None:
    a = {"kind": "payment", "summary": "pay $40", "reversible": False,
         "external": True, "cost": 40}
    assert gate_level(a) == BLOCK


def test_payment_is_block_even_if_marked_reversible_and_internal() -> None:
    # A payment can NEVER reach auto/confirm, even if mislabeled as a safe local
    # reversible action. Money-moving is a hard floor.
    a = {"kind": "payment", "summary": "charge card", "reversible": True,
         "external": False, "cost": 9.99}
    assert gate_level(a) == BLOCK


def test_money_moving_summary_blocks_non_payment_kind() -> None:
    # Kind says api_call, but the summary moves money -> still block.
    a = {"kind": "api_call", "summary": "checkout and submit order",
         "reversible": False, "external": True}
    assert gate_level(a) == BLOCK


def test_positive_cost_blocks_even_without_keyword() -> None:
    a = {"kind": "api_call", "summary": "call billing endpoint",
         "reversible": True, "external": True, "cost": 12.5}
    assert gate_level(a) == BLOCK


def test_destructive_shell_is_block() -> None:
    for cmd in ("rm -rf /tmp/x", "drop table users", "git push --force",
                "mkfs.ext4 /dev/sda", "dd if=/dev/zero of=/dev/sda"):
        a = {"kind": "shell", "summary": cmd, "reversible": True, "external": False}
        assert gate_level(a) == BLOCK, cmd


def test_safe_shell_is_confirm() -> None:
    # A non-reversible (side-effecting) but non-destructive, non-payment shell
    # command lands at confirm, not block.
    a = {"kind": "shell", "summary": "npm install", "reversible": False, "external": False}
    assert gate_level(a) == CONFIRM


def test_external_message_is_confirm() -> None:
    a = {"kind": "message", "summary": "email the team", "reversible": False,
         "external": True}
    assert gate_level(a) == CONFIRM


def test_unknown_kind_gated_on_flags() -> None:
    # An unknown kind is allowed but gated by its flags.
    auto = {"kind": "weird", "summary": "do thing", "reversible": True, "external": False}
    confirm = {"kind": "weird", "summary": "do thing", "reversible": True, "external": True}
    assert gate_level(auto) == AUTO
    assert gate_level(confirm) == CONFIRM


# ---- fail-closed: malformed / sparse inputs never become auto ----


def test_empty_dict_fails_closed_to_confirm() -> None:
    # No flags at all -> normalize assumes irreversible + external -> confirm.
    assert gate_level({}) == CONFIRM


def test_non_dict_inputs_fail_closed() -> None:
    for junk in (None, "rm something", 42, ["edit"]):
        assert gate_level(junk) != AUTO


def test_missing_reversible_defaults_cautious() -> None:
    # external=False given, but reversible omitted -> not auto.
    a = {"kind": "edit", "summary": "touch file", "external": False}
    assert gate_level(a) == CONFIRM


def test_string_flags_are_honored() -> None:
    a = {"kind": "edit", "summary": "edit", "reversible": "true", "external": "false"}
    assert gate_level(a) == AUTO


# ---------------------------------------------------------------------------
# is_payment / is_destructive
# ---------------------------------------------------------------------------


def test_is_payment_by_kind() -> None:
    assert is_payment({"kind": "payment", "summary": "x"}) is True


def test_is_payment_by_cost() -> None:
    assert is_payment({"kind": "api_call", "summary": "x", "cost": 5}) is True
    # zero / none cost alone is not a payment
    assert is_payment({"kind": "api_call", "summary": "x", "cost": 0}) is False
    assert is_payment({"kind": "api_call", "summary": "x", "cost": None}) is False


def test_is_payment_by_keyword() -> None:
    for s in ("Pay now", "complete checkout", "Place order", "Buy ticket",
              "Submit order", "confirm and pay", "book now", "wire $40 to acct",
              "transfer funds", "issue a refund", "set up billing"):
        assert is_payment({"kind": "browser", "summary": s}) is True, s


def test_is_payment_keyword_in_details() -> None:
    a = {"kind": "browser", "summary": "click button",
         "details": {"button_label": "Complete purchase"}}
    assert is_payment(a) is True


def test_non_payment_actions() -> None:
    for s in ("edit file", "read page", "navigate to site", "fill name",
              "select seat", "run tests"):
        assert is_payment({"kind": "browser", "summary": s}) is False, s


def test_is_destructive() -> None:
    assert is_destructive({"kind": "shell", "summary": "rm -rf build"}) is True
    assert is_destructive({"kind": "shell", "summary": "DROP TABLE users"}) is True
    assert is_destructive({"kind": "shell", "summary": "ls -la"}) is False


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------


def test_describe_plain_internal_has_no_tags() -> None:
    a = {"kind": "edit", "summary": "edit app.py", "reversible": True, "external": False}
    d = describe(a)
    assert d == "ronin wants to: edit app.py"
    assert "[" not in d  # no tag bracket for a plain internal reversible move


def test_describe_external_irreversible_tags() -> None:
    a = {"kind": "message", "summary": "email the team", "reversible": False,
         "external": True}
    d = describe(a)
    assert d.startswith("ronin wants to: email the team")
    assert "external" in d
    assert "irreversible" in d


def test_describe_payment_shows_money_and_cost() -> None:
    a = {"kind": "payment", "summary": "pay $40 for flight", "reversible": False,
         "external": True, "cost": 40}
    d = describe(a)
    assert "ronin wants to: pay $40 for flight" in d
    assert "MOVES MONEY" in d
    assert "costs $40" in d
    assert "irreversible" in d


def test_describe_is_one_line_with_spec_separator() -> None:
    # The spec's format uses a middle-dot separator between tags:
    #   [external · costs $X · irreversible]
    a = {"kind": "payment", "summary": "charge", "cost": 9.5,
         "reversible": False, "external": True}
    d = describe(a)
    assert "\n" not in d            # always a single line
    assert " · " in d               # the spec'd tag separator
    # the human-facing text (summary) stays plain ASCII
    a["summary"].encode("ascii")


def test_describe_destructive_tag() -> None:
    a = {"kind": "shell", "summary": "rm -rf /tmp/x", "reversible": False, "external": False}
    assert "DESTRUCTIVE" in describe(a)


# ---------------------------------------------------------------------------
# approve — the one impure entry point
# ---------------------------------------------------------------------------


class FakeConsole:
    """A minimal Rich-console stand-in: records printed output and feeds canned
    input lines to ``.input()``."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.printed: list[str] = []

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(str(a) for a in args))

    def input(self, prompt=""):
        self.printed.append(str(prompt))
        if not self._answers:
            raise EOFError  # no more input -> simulate EOF (must deny)
        return self._answers.pop(0)


def _auto_edit():
    return {"kind": "edit", "summary": "edit app.py", "reversible": True, "external": False}


def test_auto_with_auto_ok_runs_without_asking() -> None:
    # No answers queued; if it tried to prompt, FakeConsole.input would EOF.
    console = FakeConsole([])
    assert approve(_auto_edit(), console=console, auto_ok=True) is True
    # It never prompted.
    assert not any("Proceed" in p for p in console.printed)


def test_auto_without_auto_ok_still_asks() -> None:
    console = FakeConsole(["y"])
    assert approve(_auto_edit(), console=console, auto_ok=False) is True
    assert any("Proceed" in p for p in console.printed)


def test_confirm_always_asks_and_yes_proceeds() -> None:
    a = {"kind": "browser", "summary": "click Search", "reversible": True, "external": True}
    console = FakeConsole(["y"])
    # auto_ok must NOT skip a confirm-level prompt.
    assert approve(a, console=console, auto_ok=True) is True
    assert any("Proceed" in p for p in console.printed)


def test_confirm_no_denies() -> None:
    a = {"kind": "browser", "summary": "click Search", "reversible": True, "external": True}
    console = FakeConsole(["n"])
    assert approve(a, console=console) is False


def test_block_payment_cannot_be_auto_approved() -> None:
    a = {"kind": "payment", "summary": "pay $40", "reversible": False,
         "external": True, "cost": 40}
    # Even with auto_ok=True, a block action must prompt (and we answer no here).
    console = FakeConsole(["n"])
    assert approve(a, console=console, auto_ok=True) is False
    # It warned that this moves money.
    assert any("MOVES MONEY" in p for p in console.printed)


def test_block_payment_explicit_yes_proceeds_but_warns() -> None:
    a = {"kind": "payment", "summary": "pay $40", "reversible": False,
         "external": True, "cost": 40}
    console = FakeConsole(["y"])
    assert approve(a, console=console, auto_ok=True) is True
    assert any("MOVES MONEY" in p for p in console.printed)


def test_block_destructive_warns() -> None:
    a = {"kind": "shell", "summary": "rm -rf /tmp/x", "reversible": True, "external": False}
    console = FakeConsole(["n"])
    assert approve(a, console=console, auto_ok=True) is False
    assert any("DESTRUCTIVE" in p or "irreversible" in p for p in console.printed)


def test_default_deny_on_empty_answer() -> None:
    a = {"kind": "browser", "summary": "click Search", "reversible": True, "external": True}
    console = FakeConsole([""])  # empty line
    assert approve(a, console=console) is False


def test_default_deny_on_eof() -> None:
    a = {"kind": "browser", "summary": "click Search", "reversible": True, "external": True}
    console = FakeConsole([])  # no input -> EOF
    assert approve(a, console=console) is False


def test_approve_without_console_uses_builtin_input(monkeypatch) -> None:
    a = {"kind": "browser", "summary": "click Search", "reversible": True, "external": True}
    monkeypatch.setattr(builtins, "input", lambda prompt="": "y")
    assert approve(a) is True
    # And default-deny when input raises EOFError (no TTY).
    def _eof(prompt=""):
        raise EOFError
    monkeypatch.setattr(builtins, "input", _eof)
    assert approve(a) is False


# ---------------------------------------------------------------------------
# normalize — fail-closed coercion
# ---------------------------------------------------------------------------


def test_normalize_non_dict_is_worst_case() -> None:
    a = normalize("rm -rf /")
    assert a["reversible"] is False
    assert a["external"] is True
    assert a["kind"] == "unknown"


def test_normalize_fills_summary_from_kind_and_target() -> None:
    a = normalize({"kind": "edit", "target": "app.py"})
    assert "edit" in a["summary"] and "app.py" in a["summary"]


def test_normalize_bad_cost_becomes_none_not_zero() -> None:
    assert normalize({"kind": "x", "summary": "y", "cost": "free"})["cost"] is None
    assert normalize({"kind": "x", "summary": "y", "cost": float("nan")})["cost"] is None
    # negative cost is normalized to its magnitude
    assert normalize({"kind": "x", "summary": "y", "cost": -5})["cost"] == 5.0
