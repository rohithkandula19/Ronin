"""``csk briefing`` — the one killer demo.

Generates a Monday-morning founder briefing from the configured services:
revenue summary, churn watch, payment failures, top engineering issues,
recommended actions. Output is structured Markdown so it pastes cleanly into
Slack, email, or a doc.

Two execution paths:

1. **Offline / demo mode** (no API key): a deterministic Python aggregator
   over the demo dataset. Always produces a non-trivial briefing.
2. **Real provider** (Claude / Ollama / OpenAI / etc.): the agent is asked to
   write the briefing using its registered tools. The structured-data path
   above is also computed and passed to the agent as context so it can quote
   exact numbers without re-querying.

This module also exposes ``compute_briefing_data(tools)`` for tests and for
``csk ask "weekly briefing"`` to call directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ronin_agent_patterns import AgentResult, Step, Tool


# ---------- Pure-Python aggregator ----------

@dataclass
class BriefingData:
    """Structured summary computed deterministically from the tools."""

    today_iso: str = ""
    # Revenue
    mrr_cents: int = 0
    arr_cents: int = 0
    active_subs: list[dict] = field(default_factory=list)
    new_subs_7d: list[dict] = field(default_factory=list)
    churned_subs_7d: list[dict] = field(default_factory=list)
    past_due_subs: list[dict] = field(default_factory=list)
    # Payments
    failed_charges_7d: list[dict] = field(default_factory=list)
    refunded_charges_7d: list[dict] = field(default_factory=list)
    succeeded_charges_7d: list[dict] = field(default_factory=list)
    # Engineering — Linear convention: priority=1 is Urgent, 2 is High
    urgent_open_issues: list[dict] = field(default_factory=list)
    high_open_issues: list[dict] = field(default_factory=list)
    in_progress_issues: list[dict] = field(default_factory=list)

    @property
    def p0_open_issues(self) -> list[dict]:
        """Back-compat alias for urgent_open_issues."""
        return self.urgent_open_issues

    @property
    def p1_open_issues(self) -> list[dict]:
        """Back-compat alias for high_open_issues."""
        return self.high_open_issues


def _find(tools: list[Tool], name: str) -> Tool | None:
    return next((t for t in tools if t.name == name), None)


def _is_recent(record: dict, days: int, *, key: str = "created") -> bool:
    """True if ``record[key]`` (epoch seconds) is within the last ``days``."""
    from .demo_data import REFERENCE_NOW

    ts = record.get(key)
    if not isinstance(ts, (int, float)):
        return False
    return ts >= REFERENCE_NOW - days * 86_400


def compute_briefing_data(tools: list[Tool]) -> BriefingData:
    """Compute the briefing's structured data from registered tools.

    Tolerant of missing tools — fields simply stay empty.
    """
    data = BriefingData(today_iso=datetime.now(timezone.utc).date().isoformat())

    # Stripe — subscriptions
    sub_tool = _find(tools, "stripe_list_subscriptions")
    if sub_tool is not None:
        try:
            data.active_subs = sub_tool.handler(status="active", limit=100) or []
        except Exception:  # noqa: BLE001
            pass
        try:
            canceled = sub_tool.handler(status="canceled", limit=100) or []
        except Exception:  # noqa: BLE001
            canceled = []
        try:
            past_due = sub_tool.handler(status="past_due", limit=100) or []
        except Exception:  # noqa: BLE001
            past_due = []
        data.past_due_subs = past_due
        data.new_subs_7d = [s for s in data.active_subs if _is_recent(s, 7, key="created")]
        data.churned_subs_7d = [s for s in canceled if _is_recent(s, 7, key="canceled_at")]
        data.mrr_cents = sum(s.get("amount", 0) for s in data.active_subs)
        data.arr_cents = data.mrr_cents * 12

    # Stripe — charges
    ch_tool = _find(tools, "stripe_list_charges")
    if ch_tool is not None:
        try:
            charges = ch_tool.handler(limit=100) or []
        except Exception:  # noqa: BLE001
            charges = []
        recent = [c for c in charges if _is_recent(c, 7)]
        data.failed_charges_7d = [c for c in recent if c.get("status") == "failed"]
        data.refunded_charges_7d = [c for c in recent if c.get("status") == "refunded"]
        data.succeeded_charges_7d = [c for c in recent if c.get("status") == "succeeded"]

    # Linear — issues
    issues_tool = _find(tools, "linear_list_issues")
    if issues_tool is not None:
        all_issues: list[dict] = []
        for state in ("Todo", "In Progress", "In Review"):
            try:
                all_issues.extend(issues_tool.handler(state=state, limit=100) or [])
            except Exception:  # noqa: BLE001
                continue
        data.urgent_open_issues = [i for i in all_issues if i.get("priority") == 1]
        data.high_open_issues = [i for i in all_issues if i.get("priority") == 2]
        data.in_progress_issues = [i for i in all_issues if i.get("state", {}).get("name") == "In Progress"]

    return data


# ---------- Markdown renderer ----------

def _fmt_money(cents: int) -> str:
    return f"${cents / 100:,.0f}"


_PRIORITY_LABELS = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}


def _issue_line(issue: dict) -> str:
    pri = issue.get("priority")
    pri_label = _PRIORITY_LABELS.get(pri, "P?") if isinstance(pri, int) else "P?"
    assignee = (issue.get("assignee") or {}).get("name") or "unassigned"
    return f"  - **{issue.get('identifier', '?')}** ({pri_label}, {issue.get('state', {}).get('name', '?')}, {assignee}) — {issue.get('title', '')}"


def render_briefing_md(data: BriefingData) -> str:
    """Render the briefing as Markdown."""
    sections: list[str] = []
    sections.append(f"# Founder briefing — {data.today_iso}")

    # Revenue
    rev: list[str] = ["## 💰 Revenue"]
    rev.append(f"- **MRR:** {_fmt_money(data.mrr_cents)} (ARR ~{_fmt_money(data.arr_cents)})")
    rev.append(f"- **Active subscriptions:** {len(data.active_subs)}")
    rev.append(f"- **New this week:** {len(data.new_subs_7d)}")
    if data.new_subs_7d:
        for s in data.new_subs_7d:
            rev.append(f"  - {s.get('plan', '?')} for `{s.get('customer', '?')}` ({_fmt_money(s.get('amount', 0))}/mo)")
    rev.append(f"- **Churned this week:** {len(data.churned_subs_7d)}")
    if data.churned_subs_7d:
        for s in data.churned_subs_7d:
            rev.append(f"  - ⚠️  {s.get('plan', '?')} `{s.get('customer', '?')}` (ARR loss {_fmt_money(s.get('amount', 0) * 12)})")
    sections.append("\n".join(rev))

    # Payments
    pay: list[str] = ["## 💳 Payments (last 7 days)"]
    pay.append(
        f"- {len(data.succeeded_charges_7d)} succeeded · "
        f"**{len(data.failed_charges_7d)} failed** · "
        f"{len(data.refunded_charges_7d)} refunded"
    )
    if data.failed_charges_7d:
        pay.append("- Failed charges to retry:")
        for c in data.failed_charges_7d:
            reason = c.get("failure_message") or "unknown"
            pay.append(f"  - 🔁 `{c.get('customer', '?')}` ({_fmt_money(c.get('amount', 0))}) — {reason}")
    if data.past_due_subs:
        pay.append(f"- ⚠️  **{len(data.past_due_subs)} subscription(s) past due** — at risk of churn")
        for s in data.past_due_subs:
            pay.append(f"  - `{s.get('customer', '?')}` ({s.get('plan', '?')}, {_fmt_money(s.get('amount', 0))}/mo)")
    sections.append("\n".join(pay))

    # Engineering
    eng: list[str] = ["## 🛠 Engineering"]
    eng.append(
        f"- **Urgent open:** {len(data.urgent_open_issues)} · "
        f"**High open:** {len(data.high_open_issues)} · "
        f"In-progress: {len(data.in_progress_issues)}"
    )
    if data.urgent_open_issues:
        eng.append("- **Urgent to unblock:**")
        for i in data.urgent_open_issues:
            eng.append(_issue_line(i))
    if data.high_open_issues:
        eng.append("- High priority on deck:")
        for i in data.high_open_issues[:5]:
            eng.append(_issue_line(i))
    sections.append("\n".join(eng))

    # Action items (computed)
    actions: list[str] = ["## ✅ Suggested action items"]
    if data.churned_subs_7d:
        actions.append("- Reach out to recently churned customers for exit interviews.")
    if data.failed_charges_7d or data.past_due_subs:
        actions.append("- Retry failed payments / dunning flow for past-due subs.")
    if data.urgent_open_issues:
        actions.append("- Unblock or escalate every Urgent (P1) issue.")
    if not actions[1:]:
        actions.append("- Nothing urgent. Use the time to ship.")
    sections.append("\n".join(actions))

    return "\n\n".join(sections) + "\n"


# ---------- AgentResult adapter ----------

def briefing_as_agent_result(data: BriefingData, output: str) -> AgentResult:
    """Wrap the offline briefing in the same ``AgentResult`` shape as ``run_ask`` returns."""
    trace = [
        Step(kind="thought", content="Computing weekly founder briefing from configured services."),
        Step(kind="tool_call", content={"name": "stripe_list_subscriptions", "input": {"status": "active"}}),
        Step(kind="tool_result", content={"count": len(data.active_subs), "mrr_cents": data.mrr_cents}),
        Step(kind="tool_call", content={"name": "stripe_list_charges", "input": {"limit": 100}}),
        Step(kind="tool_result", content={
            "succeeded_7d": len(data.succeeded_charges_7d),
            "failed_7d": len(data.failed_charges_7d),
        }),
        Step(kind="tool_call", content={"name": "linear_list_issues", "input": {"state": "In Progress"}}),
        Step(kind="tool_result", content={
            "p0_open": len(data.p0_open_issues),
            "p1_open": len(data.p1_open_issues),
        }),
        Step(kind="final", content=output),
    ]
    return AgentResult(success=True, output=output, iterations=1, trace=trace)
