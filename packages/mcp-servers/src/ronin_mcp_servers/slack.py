"""Slack read-only MCP server reference.

Wraps Slack's Web API behind safe, agent-friendly tools. Read-only by design;
``post_message`` is intentionally absent — gate it through ``ApprovalGate`` from
``ronin_hardening`` before adding write paths.

Auth: ``SLACK_BOT_TOKEN`` env var (a bot token starting ``xoxb-``). Scopes needed:
``channels:read``, ``channels:history``, ``users:read`` (and ``search:read`` for
``search_messages``).

Tools shipped:
- ``list_channels(limit, types)``
- ``channel_history(channel_id, limit)``
- ``list_users(limit)``
- ``search_messages(query, count)`` (requires user-token scope ``search:read``)
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

SLACK_BASE = "https://slack.com/api"


class SlackReadOnlyTools(BaseModel):
    """Slack Web API wrapper (read-only methods only)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    bot_token: str = Field(default_factory=lambda: os.environ.get("SLACK_BOT_TOKEN", ""))
    user_token: str | None = None  # for search_messages
    http: Any = None
    base_url: str = SLACK_BASE
    max_limit: int = 200

    def _client(self) -> httpx.Client:
        if self.http is not None:
            return self.http
        if not self.bot_token:
            raise RuntimeError("SLACK_BOT_TOKEN not set; pass bot_token= or set the env var")
        return httpx.Client(base_url=self.base_url, timeout=30)

    def _call(self, method: str, params: dict[str, Any], *, use_user_token: bool = False) -> dict[str, Any]:
        token = self.user_token if use_user_token else self.bot_token
        if not token:
            raise RuntimeError(f"missing token for slack method {method!r}")
        response = self._client().post(
            f"/{method}",
            data=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"slack {method} failed: {body.get('error', 'unknown')}")
        return body

    def _clamp(self, limit: int) -> int:
        return max(1, min(self.max_limit, int(limit)))

    def list_channels(self, limit: int = 100, types: str = "public_channel") -> list[dict[str, Any]]:
        body = self._call("conversations.list", {"limit": self._clamp(limit), "types": types})
        return body.get("channels", [])

    def channel_history(self, channel_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if not channel_id:
            raise ValueError("channel_id is required")
        body = self._call(
            "conversations.history",
            {"channel": channel_id, "limit": self._clamp(limit)},
        )
        return body.get("messages", [])

    def list_users(self, limit: int = 100) -> list[dict[str, Any]]:
        body = self._call("users.list", {"limit": self._clamp(limit)})
        return body.get("members", [])

    def search_messages(self, query: str, count: int = 20) -> list[dict[str, Any]]:
        if not query:
            raise ValueError("query is required")
        body = self._call(
            "search.messages",
            {"query": query, "count": min(100, max(1, int(count)))},
            use_user_token=True,
        )
        return body.get("messages", {}).get("matches", [])


def slack_tools(bot_token: str | None = None, user_token: str | None = None) -> dict[str, Any]:
    backend = SlackReadOnlyTools(
        bot_token=bot_token or os.environ.get("SLACK_BOT_TOKEN", ""),
        user_token=user_token,
    )
    return {
        "slack_list_channels": backend.list_channels,
        "slack_channel_history": backend.channel_history,
        "slack_list_users": backend.list_users,
        "slack_search_messages": backend.search_messages,
    }
