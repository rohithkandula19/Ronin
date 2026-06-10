"""Reference MCP server templates — all read-only by design.

Shipped:
- ``postgres`` — read-only SQL with safety check, pluggable connection backend.
- ``stripe`` — Stripe REST wrapper (customers, subscriptions, charges).
- ``linear`` — Linear GraphQL wrapper (teams, projects, issues).
- ``slack`` — Slack Web API wrapper (channels, history, users, search).
- ``notion`` — Notion REST wrapper (search, pages, databases, query).
- ``tavily`` — Tavily web-search wrapper (search + extract).
- ``github`` — GitHub REST wrapper (repos, issues, pulls, commits, code search).
"""
from .github_server import GitHubReadOnlyTools, github_tools
from .linear import LinearReadOnlyTools, linear_tools
from .notion import NotionReadOnlyTools, notion_tools
from .postgres import (
    DangerousSQLError,
    PostgresQueryTool,
    is_readonly_sql,
    run_query,
)
from .slack import SlackReadOnlyTools, slack_tools
from .stripe import StripeReadOnlyTools, stripe_tools
from .tavily import TavilyTools, tavily_tools

__all__ = [
    "DangerousSQLError",
    "GitHubReadOnlyTools",
    "LinearReadOnlyTools",
    "NotionReadOnlyTools",
    "PostgresQueryTool",
    "SlackReadOnlyTools",
    "StripeReadOnlyTools",
    "TavilyTools",
    "github_tools",
    "is_readonly_sql",
    "linear_tools",
    "notion_tools",
    "run_query",
    "slack_tools",
    "stripe_tools",
    "tavily_tools",
]
