"""Compiling standing orders into authority a gate can enforce.

The test that matters most here is
``test_a_broad_deny_would_lose_to_a_narrow_allow``: it pins the reason a withheld
capability removes tools rather than adding a deny rule. Without it, someone
"simplifies" the removal into a deny and the wall quietly stops being one.
"""

from __future__ import annotations

from typing import Any

import pytest
from retainer_harness import laptop, orders, retainer, rule, server

from ronin.retainer.model import Capability, StandingOrders
from ronin.retainer.orders import (
    ORDERS_SOURCE,
    Authority,
    authority_for,
    compile_orders,
    describe,
    parse_grants,
)
from ronin.safety.policy import (
    UNATTENDED_SOURCE,
    AnyUse,
    Decision,
    MatchTarget,
    Rule,
    RuleSet,
    unattended_rules,
)

#: What the application layer would pass in. Kept local to the test on purpose —
#: the module under test must not carry its own copy. See its docstring.
CAPABILITY_TOOLS = {
    Capability.SHELL: frozenset({"bash"}),
    Capability.NETWORK: frozenset({"web_fetch", "web_search"}),
    Capability.BROWSER: frozenset({"browse"}),
}


# --------------------------------------------------------------------------- #
# The default floor
# --------------------------------------------------------------------------- #


def test_the_orders_default_becomes_the_rulesets_default() -> None:
    """Ask, not deny — see `StandingOrders.default`. Deny cannot escalate."""
    assert compile_orders(orders(), laptop()).ruleset.default is Decision.ASK
    strict = orders(default=Decision.DENY)
    assert compile_orders(strict, laptop()).ruleset.default is Decision.DENY


def test_orders_start_from_the_builtin_floor_rather_than_replacing_it() -> None:
    authority = compile_orders(orders(), laptop())
    assert any(r.source == "builtin" for r in authority.ruleset.rules)


def test_orders_are_appended_so_they_win_among_equals() -> None:
    mine = rule("read", Decision.ALLOW)
    authority = compile_orders(orders(grants=(mine,)), laptop())
    assert authority.ruleset.rules[-1] is mine


def test_a_caller_can_supply_its_own_base_ruleset() -> None:
    """The base contributes rules; the *default* always comes from the orders."""
    bare = RuleSet(rules=(), default=Decision.DENY)
    authority = compile_orders(orders(), laptop(), base=bare, narrow_unattended=False)
    assert authority.ruleset.rules == ()
    assert authority.ruleset.default is Decision.ASK


# --------------------------------------------------------------------------- #
# The unattended narrowings
# --------------------------------------------------------------------------- #


def compiled(**kwargs: Any) -> RuleSet:
    return compile_orders(orders(), laptop(), **kwargs).ruleset


def test_a_retainer_gets_the_unattended_narrowings_by_default() -> None:
    """A Retainer is unattended by definition, so the narrowing is not opt-in.

    What it takes back is covered where it lives, in ``tests/safety``. Here the
    claim is only that a compiled authority carries it without being asked.
    """
    sources = {rule.source for rule in compiled().rules}
    assert UNATTENDED_SOURCE in sources
    assert set(unattended_rules()) <= set(compiled().rules)


def test_the_narrowings_can_be_turned_off_for_the_whole_deployment() -> None:
    """The operator-level escape hatch. Deliberately not reachable from the orders:
    a floor the restricted thing can edit is not a floor."""
    assert not any(
        rule.source == UNATTENDED_SOURCE for rule in compiled(narrow_unattended=False).rules
    )


def test_orders_still_come_last_with_the_narrowings_in_place() -> None:
    """The narrowings sit with the floor they narrow. Otherwise "orders are appended,
    so they win among equals" would be true everywhere except here."""
    mine = rule("read", Decision.ALLOW)
    assert compile_orders(orders(grants=(mine,)), laptop()).ruleset.rules[-1] is mine


def test_a_grant_cannot_punch_a_hole_in_a_narrowing() -> None:
    """Appending wins among *equals*; restrictiveness is settled first. A Retainer
    that writes itself an ``npm publish`` allowance still gets asked."""
    allowance = rule("bash", Decision.ALLOW, command="^npm publish")
    ruleset = compile_orders(orders(grants=(allowance,)), laptop()).ruleset
    target = MatchTarget(tool="bash", arguments={"command": "npm publish"})
    assert ruleset.resolve(target).decision is Decision.ASK


# --------------------------------------------------------------------------- #
# The capability wall — why removal and not denial
# --------------------------------------------------------------------------- #


def test_a_broad_deny_would_lose_to_a_narrow_allow() -> None:
    """The reason the wall is tool removal. Precedence is specificity first.

    If a withheld capability were expressed as ``deny bash`` tool-wide, orders
    could out-specify it one glob at a time and the wall would leak silently.
    """
    broad = Rule(tool="bash", matcher=AnyUse(), decision=Decision.DENY, unwaivable=True)
    narrow = rule("bash", Decision.ALLOW, command="^curl ")
    ruleset = RuleSet(rules=(broad, narrow), default=Decision.DENY)
    target = MatchTarget(tool="bash", arguments={"command": "curl https://example.com"})
    assert ruleset.resolve(target).decision is Decision.ALLOW


def test_a_withheld_capability_takes_its_tools_out_of_the_published_set() -> None:
    wants = orders(
        tools=frozenset({"read", "bash", "browse"}),
        wants=frozenset({Capability.SHELL, Capability.BROWSER}),
    )
    hosted = compile_orders(wants, server(), capability_tools=CAPABILITY_TOOLS)
    assert not hosted.permits("browse")
    assert hosted.permits("bash")
    assert hosted.permits("read")


def test_the_same_orders_keep_the_tool_on_a_deployment_that_holds_it() -> None:
    wants = orders(tools=frozenset({"read", "browse"}), wants=frozenset({Capability.BROWSER}))
    assert compile_orders(wants, laptop(), capability_tools=CAPABILITY_TOOLS).permits("browse")
    assert not compile_orders(wants, server(), capability_tools=CAPABILITY_TOOLS).permits("browse")


def test_removal_is_explained_rather_than_silent() -> None:
    wants = orders(tools=frozenset({"read", "browse"}), wants=frozenset({Capability.BROWSER}))
    hosted = compile_orders(wants, server(), capability_tools=CAPABILITY_TOOLS)
    joined = "\n".join(hosted.notes)
    assert "browse is not published" in joined
    assert "browser capability" in joined
    assert "fly does not hold it" in joined


def test_a_capability_nobody_asked_for_removes_nothing() -> None:
    quiet = orders(tools=frozenset({"read", "browse"}))
    hosted = compile_orders(quiet, server(), capability_tools=CAPABILITY_TOOLS)
    assert hosted.permits("browse")
    assert hosted.notes == ()


def test_a_tool_not_in_the_orders_is_not_reported_as_removed() -> None:
    wants = orders(tools=frozenset({"read"}), wants=frozenset({Capability.BROWSER}))
    hosted = compile_orders(wants, server(), capability_tools=CAPABILITY_TOOLS)
    assert not any("is not published" in note for note in hosted.notes)
    assert any("browser capability" in note for note in hosted.notes)


def test_the_module_keeps_no_tool_names_of_its_own() -> None:
    """Passing nothing removes nothing: it must not guess which tool is which."""
    wants = orders(tools=frozenset({"browse"}), wants=frozenset({Capability.BROWSER}))
    assert compile_orders(wants, server()).permits("browse")


def test_granted_and_withheld_are_reported_from_the_intersection() -> None:
    wants = orders(wants=frozenset({Capability.SHELL, Capability.BROWSER}))
    hosted = compile_orders(wants, server(), capability_tools=CAPABILITY_TOOLS)
    assert hosted.granted == frozenset({Capability.SHELL})
    assert hosted.withheld == frozenset({Capability.BROWSER})
    assert hosted.holds(Capability.SHELL)
    assert not hosted.holds(Capability.BROWSER)


# --------------------------------------------------------------------------- #
# One permission language, not two
# --------------------------------------------------------------------------- #


def test_orders_use_the_syntax_settings_json_already_uses() -> None:
    rules = parse_grants(
        [
            {"tool": "bash", "decision": "allow", "command": "^pytest"},
            {"tool": "write", "decision": "deny", "path": "migrations/**"},
            {"tool": "read", "decision": "allow"},
        ]
    )
    assert [r.tool for r in rules] == ["bash", "write", "read"]
    assert [r.decision for r in rules] == [Decision.ALLOW, Decision.DENY, Decision.ALLOW]
    assert rules[2].matcher.specificity == 0


def test_parsed_orders_carry_their_provenance() -> None:
    (parsed,) = parse_grants([{"tool": "read", "decision": "allow"}])
    assert parsed.source == ORDERS_SOURCE


def test_a_bad_order_names_its_position_in_the_list() -> None:
    entries = [
        {"tool": "read", "decision": "allow"},
        {"tool": "bash", "decision": "allow", "command": "^(unclosed"},
    ]
    with pytest.raises(ValueError, match="standing order #2"):
        parse_grants(entries)


def test_a_path_glob_means_here_what_it_means_in_settings() -> None:
    (parsed,) = parse_grants([{"tool": "write", "decision": "allow", "path": "src/*"}])
    crosses = MatchTarget(tool="write", arguments={"path": "src/a/b.py"})
    stays = MatchTarget(tool="write", arguments={"path": "src/b.py"})
    assert not parsed.matches(crosses)
    assert parsed.matches(stays)


# --------------------------------------------------------------------------- #
# The blanket-allow invariant, now guarding the set rather than the clause
# --------------------------------------------------------------------------- #


def test_a_blanket_allow_is_refused_in_standing_orders() -> None:
    blanket = parse_grants([{"tool": "*", "decision": "allow"}])
    with pytest.raises(ValueError, match="blanket allow"):
        StandingOrders(grants=blanket)


def test_a_narrowed_wildcard_allow_is_fine() -> None:
    narrowed = parse_grants([{"tool": "*", "decision": "allow", "path": "src/**"}])
    assert StandingOrders(grants=narrowed).grants == narrowed


def test_a_blanket_deny_is_the_point_of_a_wildcard() -> None:
    floor = parse_grants([{"tool": "*", "decision": "deny"}])
    assert StandingOrders(grants=floor).grants == floor


# --------------------------------------------------------------------------- #
# Whole-Retainer entry point and the operator-facing summary
# --------------------------------------------------------------------------- #


def test_authority_for_compiles_the_retainers_own_orders() -> None:
    mine = rule("read", Decision.ALLOW)
    it = retainer(orders=orders(grants=(mine,), tools=frozenset({"read"})))
    assert authority_for(it, laptop()).permits("read")


def test_describe_leads_with_what_is_published_and_what_went_away() -> None:
    wants = orders(
        tools=frozenset({"read", "browse"}),
        wants=frozenset({Capability.BROWSER, Capability.SHELL}),
        grants=parse_grants([{"tool": "read", "decision": "allow"}]),
    )
    text = describe(compile_orders(wants, server(), capability_tools=CAPABILITY_TOOLS))
    assert text.splitlines()[0] == "tools: read"
    assert "capabilities: shell" in text
    assert "browse is not published" in text
    assert "allow read where any use of this tool" in text
    assert text.endswith("anything else: ask")


def test_describe_says_none_rather_than_nothing_when_no_tool_is_published() -> None:
    assert describe(Authority(ruleset=RuleSet(), tools=frozenset())).startswith("tools: (none)")


def test_describe_lists_only_the_orders_not_the_builtin_floor() -> None:
    text = describe(compile_orders(orders(), laptop()))
    assert "builtin" not in text
