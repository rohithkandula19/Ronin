from __future__ import annotations

from ronin_cli.config import RoninConfig
from ronin_cli.tools import build_tools


def test_demo_mode_builds_demo_tools() -> None:
    tools = build_tools(RoninConfig(demo_mode=True))
    names = {t.name for t in tools}
    assert "stripe_list_customers" in names
    assert "linear_list_issues" in names
    # Slack/Notion/Postgres not wired in demo (no demo data needed for the showcase)
    assert "slack_list_channels" not in names


def test_demo_stripe_list_customers_filters_by_email() -> None:
    tools = build_tools(RoninConfig(demo_mode=True))
    list_customers = next(t for t in tools if t.name == "stripe_list_customers")
    customers = list_customers.handler(email="alice@acme.com")
    assert len(customers) == 1
    assert customers[0]["email"] == "alice@acme.com"


def test_demo_stripe_subscriptions_filter_by_status() -> None:
    tools = build_tools(RoninConfig(demo_mode=True))
    list_subs = next(t for t in tools if t.name == "stripe_list_subscriptions")
    active = list_subs.handler(status="active")
    canceled = list_subs.handler(status="canceled")
    past_due = list_subs.handler(status="past_due")
    # Demo dataset bumped to make 'csk briefing' interesting — exercise the filters
    # generically rather than locking exact counts.
    assert all(s["status"] == "active" for s in active)
    assert all(s["status"] == "canceled" for s in canceled)
    assert all(s["status"] == "past_due" for s in past_due)
    assert len(active) >= 1
    assert len(canceled) >= 1
    assert len(past_due) >= 1


def test_demo_linear_get_issue() -> None:
    tools = build_tools(RoninConfig(demo_mode=True))
    get_issue = next(t for t in tools if t.name == "linear_get_issue")
    issue = get_issue.handler(identifier="ENG-101")
    assert "Stripe webhook" in issue["title"]
    assert issue["identifier"] == "ENG-101"


def test_real_mode_with_no_creds_returns_empty() -> None:
    tools = build_tools(RoninConfig())
    assert tools == []


def test_real_mode_partial_creds_only_wires_those_services() -> None:
    tools = build_tools(RoninConfig(stripe_api_key="rk_x"))
    names = {t.name for t in tools}
    assert "stripe_list_customers" in names
    assert "linear_list_issues" not in names
