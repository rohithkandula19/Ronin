"""Build the tool list for the agent based on configured services.

Demo mode swaps every real backend for an in-memory fixture so users can run
``csk ask "..."`` immediately after ``csk init --demo``.
"""
from __future__ import annotations

from typing import Any, Callable

from ronin_agent_patterns import Tool
from ronin_mcp_servers import (
    LinearReadOnlyTools,
    NotionReadOnlyTools,
    SlackReadOnlyTools,
    StripeReadOnlyTools,
)

from .config import RoninConfig
from .demo_data import (
    CHARGES as DEMO_CHARGES,
    CUSTOMERS as DEMO_CUSTOMERS,
    ISSUES as DEMO_ISSUES_FULL,
    SUBSCRIPTIONS as DEMO_SUBS,
    TEAMS as DEMO_TEAMS_FULL,
)


def _wrap(name: str, description: str, schema: dict[str, Any], handler: Callable[..., Any]) -> Tool:
    return Tool(name=name, description=description, input_schema=schema, handler=handler)


# ---------- Stripe ----------


def stripe_demo_tools() -> list[Tool]:
    def list_customers(limit: int = 10, email: str | None = None) -> list:
        out = DEMO_CUSTOMERS
        if email:
            out = [c for c in out if c["email"] == email]
        return out[:limit]

    def list_subscriptions(customer_id: str | None = None, status: str | None = None, limit: int = 10) -> list:
        out = DEMO_SUBS
        if customer_id:
            out = [s for s in out if s["customer"] == customer_id]
        if status:
            out = [s for s in out if s["status"] == status]
        return out[:limit]

    def list_charges(customer_id: str | None = None, limit: int = 10) -> list:
        out = DEMO_CHARGES
        if customer_id:
            out = [c for c in out if c["customer"] == customer_id]
        return out[:limit]

    return [
        _wrap(
            "stripe_list_customers",
            "List Stripe customers, optionally filtered by email.",
            {"type": "object", "properties": {
                "limit": {"type": "integer", "default": 10},
                "email": {"type": "string", "description": "Exact-match filter on email."},
            }},
            list_customers,
        ),
        _wrap(
            "stripe_list_subscriptions",
            "List Stripe subscriptions, optionally filtered by customer or status (active|canceled|past_due).",
            {"type": "object", "properties": {
                "customer_id": {"type": "string"},
                "status": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            }},
            list_subscriptions,
        ),
        _wrap(
            "stripe_list_charges",
            "List recent Stripe charges, optionally filtered by customer.",
            {"type": "object", "properties": {
                "customer_id": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            }},
            list_charges,
        ),
    ]


def stripe_real_tools(api_key: str) -> list[Tool]:
    backend = StripeReadOnlyTools(api_key=api_key)
    return [
        _wrap(
            "stripe_list_customers",
            "List Stripe customers, optionally filtered by email.",
            {"type": "object", "properties": {"limit": {"type": "integer"}, "email": {"type": "string"}}},
            backend.list_customers,
        ),
        _wrap(
            "stripe_list_subscriptions",
            "List Stripe subscriptions, optionally filtered by customer or status.",
            {"type": "object", "properties": {
                "customer_id": {"type": "string"}, "status": {"type": "string"}, "limit": {"type": "integer"},
            }},
            backend.list_subscriptions,
        ),
        _wrap(
            "stripe_list_charges",
            "List recent Stripe charges, optionally filtered by customer.",
            {"type": "object", "properties": {"customer_id": {"type": "string"}, "limit": {"type": "integer"}}},
            backend.list_charges,
        ),
    ]


# ---------- Linear ----------

DEMO_TEAMS = DEMO_TEAMS_FULL
DEMO_ISSUES = DEMO_ISSUES_FULL


def linear_demo_tools() -> list[Tool]:
    def list_teams(limit: int = 25) -> list:
        return DEMO_TEAMS[:limit]

    def list_issues(team_id: str | None = None, state: str | None = None, limit: int = 25) -> list:
        out = DEMO_ISSUES
        if state:
            out = [i for i in out if i["state"]["name"] == state]
        return out[:limit]

    def get_issue(identifier: str) -> dict:
        for i in DEMO_ISSUES:
            if i["identifier"] == identifier:
                return i
        raise LookupError(f"issue {identifier!r} not found")

    return [
        _wrap("linear_list_teams", "List Linear teams.", {"type": "object", "properties": {"limit": {"type": "integer"}}}, list_teams),
        _wrap(
            "linear_list_issues",
            "List Linear issues, optionally filtered by team_id or state name (Todo|In Progress|In Review|Done|Canceled).",
            {"type": "object", "properties": {"team_id": {"type": "string"}, "state": {"type": "string"}, "limit": {"type": "integer"}}},
            list_issues,
        ),
        _wrap(
            "linear_get_issue",
            "Fetch one issue by its identifier (e.g. 'ENG-101').",
            {"type": "object", "properties": {"identifier": {"type": "string"}}, "required": ["identifier"]},
            get_issue,
        ),
    ]


def linear_real_tools(api_key: str) -> list[Tool]:
    backend = LinearReadOnlyTools(api_key=api_key)
    return [
        _wrap("linear_list_teams", "List Linear teams.", {"type": "object", "properties": {"limit": {"type": "integer"}}}, backend.list_teams),
        _wrap(
            "linear_list_issues",
            "List Linear issues, optionally filtered by team_id or state name.",
            {"type": "object", "properties": {"team_id": {"type": "string"}, "state": {"type": "string"}, "limit": {"type": "integer"}}},
            backend.list_issues,
        ),
        _wrap(
            "linear_get_issue",
            "Fetch one issue by its identifier (e.g. 'ENG-101').",
            {"type": "object", "properties": {"identifier": {"type": "string"}}, "required": ["identifier"]},
            backend.get_issue,
        ),
    ]


# ---------- Slack / Notion / Postgres real wiring ----------

def slack_real_tools(bot_token: str, user_token: str | None) -> list[Tool]:
    backend = SlackReadOnlyTools(bot_token=bot_token, user_token=user_token)
    return [
        _wrap("slack_list_channels", "List public Slack channels.", {"type": "object", "properties": {"limit": {"type": "integer"}}}, backend.list_channels),
        _wrap(
            "slack_channel_history",
            "Read recent messages from a Slack channel.",
            {"type": "object", "properties": {"channel_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["channel_id"]},
            backend.channel_history,
        ),
        _wrap("slack_list_users", "List Slack workspace users.", {"type": "object", "properties": {"limit": {"type": "integer"}}}, backend.list_users),
    ]


def notion_real_tools(token: str) -> list[Tool]:
    backend = NotionReadOnlyTools(token=token)
    return [
        _wrap(
            "notion_search",
            "Search Notion for pages or databases matching a query.",
            {"type": "object", "properties": {"query": {"type": "string"}, "page_size": {"type": "integer"}}, "required": ["query"]},
            backend.search,
        ),
        _wrap(
            "notion_retrieve_page",
            "Fetch a Notion page by id.",
            {"type": "object", "properties": {"page_id": {"type": "string"}}, "required": ["page_id"]},
            backend.retrieve_page,
        ),
    ]


def postgres_real_tools(database_url: str) -> list[Tool]:
    """Wired only when psycopg2 is installed (the [postgres] extra)."""
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError:
        return []
    from ronin_mcp_servers import PostgresQueryTool

    conn = psycopg2.connect(database_url)
    backend = PostgresQueryTool(connection=conn)
    return [
        _wrap(
            "postgres_query",
            backend.description,
            {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
            backend.query,
        ),
    ]


def build_tools(config: RoninConfig) -> list[Tool]:
    """Assemble the full tool list for the configured/demo services + any user plugins."""
    from .plugins import load_plugin_tools

    tools: list[Tool] = []
    if config.demo_mode:
        tools.extend(stripe_demo_tools())
        tools.extend(linear_demo_tools())
    else:
        if config.stripe_api_key:
            tools.extend(stripe_real_tools(config.stripe_api_key))
        if config.linear_api_key:
            tools.extend(linear_real_tools(config.linear_api_key))
        if config.slack_bot_token:
            tools.extend(slack_real_tools(config.slack_bot_token, config.slack_user_token))
        if config.notion_token:
            tools.extend(notion_real_tools(config.notion_token))
        if config.database_url:
            tools.extend(postgres_real_tools(config.database_url))

    # User plugins always loaded last so they can override or supplement built-ins.
    tools.extend(load_plugin_tools())
    return tools
