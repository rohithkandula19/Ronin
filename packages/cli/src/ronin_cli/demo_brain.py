"""Offline 'brain' for demo mode without an Anthropic API key.

This is NOT Claude — it's a tiny keyword router that pattern-matches the question
against demo tools and returns a believable templated answer. Lets a new user
``csk init --demo && csk ask "..."`` without setting up any credentials.

When ANTHROPIC_API_KEY is set, the real ReActAgent is used instead.
"""
from __future__ import annotations

from typing import Any

from ronin_agent_patterns import AgentResult, Step, Tool


def _find(tools: list[Tool], name: str) -> Tool | None:
    return next((t for t in tools if t.name == name), None)


def _step(kind: str, content: Any) -> Step:
    return Step(kind=kind, content=content)


def _wrap_result(output: str, trace: list[Step]) -> AgentResult:
    return AgentResult(success=True, output=output, iterations=1, trace=trace)


def _subs_answer(tools: list[Tool], note: str = "") -> AgentResult:
    tool = _find(tools, "stripe_list_subscriptions")
    if tool is None:
        return None  # type: ignore[return-value]
    active = tool.handler(status="active")
    canceled = tool.handler(status="canceled")
    mrr_cents = sum(s.get("amount", 0) for s in active)
    mrr = mrr_cents / 100
    plans = ", ".join(f"{s.get('plan', 'Unknown')} (${s.get('amount', 0)/100:.0f}/mo)" for s in active)
    output = (
        f"You have {len(active)} active subscriptions and {len(canceled)} canceled.\n"
        f"Active plans: {plans or 'none'}.\n"
        f"Total MRR from active subs: ${mrr:.0f}/mo." + (f"\n\n{note}" if note else "")
    )
    return _wrap_result(output, [
        _step("thought", "User asked about subscriptions / MRR. Calling stripe_list_subscriptions twice — once for active, once for canceled."),
        _step("tool_call", {"name": "stripe_list_subscriptions", "input": {"status": "active"}}),
        _step("tool_result", {"name": "stripe_list_subscriptions", "result": active}),
        _step("tool_call", {"name": "stripe_list_subscriptions", "input": {"status": "canceled"}}),
        _step("tool_result", {"name": "stripe_list_subscriptions", "result": canceled}),
        _step("final", output),
    ])


def _customers_answer(tools: list[Tool]) -> AgentResult:
    tool = _find(tools, "stripe_list_customers")
    if tool is None:
        return None  # type: ignore[return-value]
    customers = tool.handler(limit=20)
    listing = "\n".join(f"  • {c.get('name', '?')} <{c.get('email', '?')}>" for c in customers)
    output = f"You have {len(customers)} customers:\n{listing}"
    return _wrap_result(output, [
        _step("thought", "User asked about customers. Calling stripe_list_customers."),
        _step("tool_call", {"name": "stripe_list_customers", "input": {"limit": 20}}),
        _step("tool_result", {"name": "stripe_list_customers", "result": customers}),
        _step("final", output),
    ])


def _issues_answer(tools: list[Tool], state_filter: str | None = None) -> AgentResult:
    tool = _find(tools, "linear_list_issues")
    if tool is None:
        return None  # type: ignore[return-value]
    kwargs = {"limit": 25}
    if state_filter:
        kwargs["state"] = state_filter
    issues = tool.handler(**kwargs)
    listing = "\n".join(
        f"  • {i.get('identifier', '?')} — {i.get('title', '?')} "
        f"(state: {i.get('state', {}).get('name', '?')}, priority: {i.get('priority', '?')})"
        for i in issues
    )
    label = f"in state '{state_filter}'" if state_filter else "across all states"
    output = f"Found {len(issues)} issues {label}:\n{listing}"
    return _wrap_result(output, [
        _step("thought", f"User asked about Linear issues. Calling linear_list_issues with {kwargs}."),
        _step("tool_call", {"name": "linear_list_issues", "input": kwargs}),
        _step("tool_result", {"name": "linear_list_issues", "result": issues}),
        _step("final", output),
    ])


def _teams_answer(tools: list[Tool]) -> AgentResult:
    tool = _find(tools, "linear_list_teams")
    if tool is None:
        return None  # type: ignore[return-value]
    teams = tool.handler()
    listing = "\n".join(f"  • {t.get('key', '?')} — {t.get('name', '?')}" for t in teams)
    output = f"You have {len(teams)} Linear teams:\n{listing}"
    return _wrap_result(output, [
        _step("thought", "User asked about teams. Calling linear_list_teams."),
        _step("tool_call", {"name": "linear_list_teams", "input": {}}),
        _step("tool_result", {"name": "linear_list_teams", "result": teams}),
        _step("final", output),
    ])


def _charges_answer(tools: list[Tool]) -> AgentResult:
    tool = _find(tools, "stripe_list_charges")
    if tool is None:
        return None  # type: ignore[return-value]
    charges = tool.handler(limit=10)
    total = sum(c.get("amount", 0) for c in charges) / 100
    listing = "\n".join(
        f"  • {c.get('id', '?')} — ${c.get('amount', 0)/100:.0f} ({c.get('status', '?')}) for {c.get('customer', '?')}"
        for c in charges
    )
    output = f"Most recent {len(charges)} charges totaling ${total:.0f}:\n{listing}"
    return _wrap_result(output, [
        _step("thought", "User asked about charges / revenue. Calling stripe_list_charges."),
        _step("tool_call", {"name": "stripe_list_charges", "input": {"limit": 10}}),
        _step("tool_result", {"name": "stripe_list_charges", "result": charges}),
        _step("final", output),
    ])


def _fallback(question: str, tools: list[Tool]) -> AgentResult:
    tool_names = ", ".join(t.name for t in tools) or "(no tools registered)"
    output = (
        f"[demo brain] I'm a keyword router, not Claude. I didn't recognize a pattern in: {question!r}\n\n"
        "Try one of:\n"
        '  • "how many active subscriptions do we have?"\n'
        '  • "list our customers"\n'
        '  • "what ENG issues are in progress?"\n'
        '  • "list linear teams"\n'
        '  • "show me recent charges"\n\n'
        f"Available tools: {tool_names}\n\n"
        "For full natural-language answers, set ANTHROPIC_API_KEY and re-run."
    )
    return _wrap_result(output, [
        _step("thought", "No keyword match. Returning fallback help."),
        _step("final", output),
    ])


def demo_answer(question: str, tools: list[Tool]) -> AgentResult:
    """Route a question to the right demo handler. Used only when no API key is set."""
    q = question.lower()

    if any(k in q for k in ("subscription", "subs", "mrr", "revenue per month", "monthly recurring")):
        result = _subs_answer(tools)
        if result:
            return result

    if "customer" in q and "subscription" not in q:
        result = _customers_answer(tools)
        if result:
            return result

    if "team" in q and "linear" in q or q.strip() in ("teams", "list teams"):
        result = _teams_answer(tools)
        if result:
            return result

    if any(k in q for k in ("issue", "ticket", "ENG-", "DES-", "in progress", "todo", "in review")):
        state = None
        for candidate in ("In Progress", "Todo", "In Review", "Done", "Canceled"):
            if candidate.lower() in q:
                state = candidate
                break
        result = _issues_answer(tools, state_filter=state)
        if result:
            return result

    if any(k in q for k in ("charge", "payment", "revenue", "billed")):
        result = _charges_answer(tools)
        if result:
            return result

    return _fallback(question, tools)
