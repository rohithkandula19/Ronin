"""Custom briefing templates.

Out of the box, ``csk briefing`` renders four sections in a fixed order:
revenue / payments / engineering / actions. Some founders want to skip one,
reorder them, or add a custom heading at the top.

Drop a TOML file at ``.csk/briefing-template.toml`` (or pass ``--template
<path>``) and ``csk`` uses your layout instead:

    title = "Monday brief — {{date}}"
    sections = ["revenue", "payments", "engineering", "actions"]

Section IDs (all optional, render in the order you list):
- ``revenue``       — MRR, new subs, churn
- ``payments``      — succeeded/failed/refunded, past-due
- ``engineering``   — urgent/high open issues
- ``actions``       — computed follow-ups
- ``customers``     — full customer list (off by default)
- ``charges``       — recent charges summary (off by default)

Title is rendered with ``{{date}}`` substituted for the briefing date.
Unknown section IDs are silently skipped (so adding new ones later is safe).
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from .briefing import (
    BriefingData,
    _fmt_money,
    _issue_line,
)


DEFAULT_SECTIONS = ["revenue", "payments", "engineering", "actions"]
DEFAULT_TITLE = "Founder briefing — {{date}}"
TEMPLATE_PATH = Path(".csk") / "briefing-template.toml"


class BriefingTemplate(BaseModel):
    title: str = DEFAULT_TITLE
    sections: list[str] = Field(default_factory=lambda: list(DEFAULT_SECTIONS))

    @classmethod
    def default(cls) -> "BriefingTemplate":
        return cls()

    @classmethod
    def load(cls, path: Path | None = None) -> "BriefingTemplate":
        path = path or TEMPLATE_PATH
        if not path.exists():
            return cls.default()
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        return cls(**raw)


# ---------- section renderers ----------

def _section_revenue(data: BriefingData) -> str:
    lines = ["## 💰 Revenue"]
    lines.append(f"- **MRR:** {_fmt_money(data.mrr_cents)} (ARR ~{_fmt_money(data.arr_cents)})")
    lines.append(f"- **Active subscriptions:** {len(data.active_subs)}")
    lines.append(f"- **New this week:** {len(data.new_subs_7d)}")
    for s in data.new_subs_7d:
        lines.append(f"  - {s.get('plan', '?')} for `{s.get('customer', '?')}` ({_fmt_money(s.get('amount', 0))}/mo)")
    lines.append(f"- **Churned this week:** {len(data.churned_subs_7d)}")
    for s in data.churned_subs_7d:
        lines.append(f"  - ⚠️  {s.get('plan', '?')} `{s.get('customer', '?')}` (ARR loss {_fmt_money(s.get('amount', 0) * 12)})")
    return "\n".join(lines)


def _section_payments(data: BriefingData) -> str:
    lines = ["## 💳 Payments (last 7 days)"]
    lines.append(
        f"- {len(data.succeeded_charges_7d)} succeeded · "
        f"**{len(data.failed_charges_7d)} failed** · "
        f"{len(data.refunded_charges_7d)} refunded"
    )
    if data.failed_charges_7d:
        lines.append("- Failed charges to retry:")
        for c in data.failed_charges_7d:
            reason = c.get("failure_message") or "unknown"
            lines.append(f"  - 🔁 `{c.get('customer', '?')}` ({_fmt_money(c.get('amount', 0))}) — {reason}")
    if data.past_due_subs:
        lines.append(f"- ⚠️  **{len(data.past_due_subs)} subscription(s) past due** — at risk of churn")
        for s in data.past_due_subs:
            lines.append(f"  - `{s.get('customer', '?')}` ({s.get('plan', '?')}, {_fmt_money(s.get('amount', 0))}/mo)")
    return "\n".join(lines)


def _section_engineering(data: BriefingData) -> str:
    lines = ["## 🛠 Engineering"]
    lines.append(
        f"- **Urgent open:** {len(data.urgent_open_issues)} · "
        f"**High open:** {len(data.high_open_issues)} · "
        f"In-progress: {len(data.in_progress_issues)}"
    )
    if data.urgent_open_issues:
        lines.append("- **Urgent to unblock:**")
        for i in data.urgent_open_issues:
            lines.append(_issue_line(i))
    if data.high_open_issues:
        lines.append("- High priority on deck:")
        for i in data.high_open_issues[:5]:
            lines.append(_issue_line(i))
    return "\n".join(lines)


def _section_actions(data: BriefingData) -> str:
    lines: list[str] = ["## ✅ Suggested action items"]
    if data.churned_subs_7d:
        lines.append("- Reach out to recently churned customers for exit interviews.")
    if data.failed_charges_7d or data.past_due_subs:
        lines.append("- Retry failed payments / dunning flow for past-due subs.")
    if data.urgent_open_issues:
        lines.append("- Unblock or escalate every Urgent (P1) issue.")
    if not lines[1:]:
        lines.append("- Nothing urgent. Use the time to ship.")
    return "\n".join(lines)


def _section_customers(data: BriefingData) -> str:
    """Off by default — full active-customer list. Useful for tiny teams."""
    lines = ["## 👥 Active customers"]
    if not data.active_subs:
        lines.append("- _none_")
        return "\n".join(lines)
    for s in data.active_subs:
        lines.append(
            f"- `{s.get('customer', '?')}` — {s.get('plan', '?')} ({_fmt_money(s.get('amount', 0))}/mo)"
        )
    return "\n".join(lines)


def _section_charges(data: BriefingData) -> str:
    """Off by default — last week's charges (all statuses)."""
    lines = ["## 🧾 Charges (last 7 days)"]
    all_charges = data.succeeded_charges_7d + data.failed_charges_7d + data.refunded_charges_7d
    if not all_charges:
        lines.append("- _no charges this week_")
        return "\n".join(lines)
    for c in all_charges:
        status = c.get("status", "?")
        marker = {"succeeded": "✅", "failed": "🔁", "refunded": "↩"}.get(status, "·")
        lines.append(
            f"- {marker} `{c.get('customer', '?')}` {_fmt_money(c.get('amount', 0))} — {status}"
        )
    return "\n".join(lines)


SECTIONS: dict[str, Callable[[BriefingData], str]] = {
    "revenue": _section_revenue,
    "payments": _section_payments,
    "engineering": _section_engineering,
    "actions": _section_actions,
    "customers": _section_customers,
    "charges": _section_charges,
}


def render_with_template(data: BriefingData, template: BriefingTemplate) -> str:
    """Render the briefing using a custom template (sections + title)."""
    title = template.title.replace("{{date}}", data.today_iso)
    parts: list[str] = [f"# {title}"]
    for section_id in template.sections:
        renderer = SECTIONS.get(section_id)
        if renderer is None:
            continue  # unknown id — silently skip (forward-compat)
        parts.append(renderer(data))
    return "\n\n".join(parts) + "\n"
