"""Payload validation for incoming feed documents."""

from __future__ import annotations

from typing import Any

REQUIRED_FIELDS = ("id", "title", "url")


class FeedError(Exception):
    """Raised when a feed payload cannot be used at all."""


def collect_payload_problems(payload: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems with *payload*.

    An empty list means the payload is usable.
    """
    problems: list[str] = []
    for field in REQUIRED_FIELDS:
        value = payload.get(field)
        if value is None or value == "":
            problems.append(f"missing required field: {field}")
    items = payload.get("items")
    if items is not None and not isinstance(items, list):
        problems.append("items must be a list")
    url = payload.get("url")
    if isinstance(url, str) and not url.startswith(("http://", "https://")):
        problems.append("url must be http(s)")
    return problems
