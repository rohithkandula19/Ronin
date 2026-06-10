"""Slack delivery for ``csk briefing``.

Posts a briefing to a Slack channel via the bot-token Web API (``chat.postMessage``).
Authentication: ``SLACK_BOT_TOKEN`` (must have the ``chat:write`` scope).

Only converts a small Markdown subset → Slack mrkdwn:
- ``**bold**`` → ``*bold*``
- ``# Heading`` → ``*Heading*``  (Slack ignores #/##/###)
- bullets and code fences are left as-is (Slack renders both)
"""
from __future__ import annotations

import re
from typing import Any

import httpx

SLACK_POST_URL = "https://slack.com/api/chat.postMessage"


def to_slack_mrkdwn(markdown: str) -> str:
    """Convert the briefing's Markdown subset to Slack mrkdwn."""
    out = markdown
    # **bold** → *bold*
    out = re.sub(r"\*\*(.+?)\*\*", r"*\1*", out)
    # # H1 / ## H2 / ### H3 → *H*
    out = re.sub(r"^#{1,3}\s+(.+)$", r"*\1*", out, flags=re.MULTILINE)
    return out


def post_briefing_to_slack(
    bot_token: str,
    channel: str,
    markdown_body: str,
    *,
    http: Any | None = None,
) -> dict[str, Any]:
    """POST the briefing to a Slack channel.

    Returns Slack's JSON response on success. Raises:
      - ValueError if ``bot_token`` or ``channel`` is missing
      - RuntimeError if Slack returns ``ok: false``
      - httpx.HTTPStatusError if the call is rejected at the HTTP layer
    """
    if not bot_token:
        raise ValueError("bot_token is required (set SLACK_BOT_TOKEN or pass via config)")
    if not channel:
        raise ValueError("channel is required (e.g. '#founders' or a channel ID 'C1234ABCD')")

    client = http if http is not None else httpx.Client(timeout=30)
    response = client.post(
        SLACK_POST_URL,
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "channel": channel,
            "text": to_slack_mrkdwn(markdown_body),
            "mrkdwn": True,
        },
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"slack chat.postMessage failed: {body.get('error', 'unknown')}")
    return body
