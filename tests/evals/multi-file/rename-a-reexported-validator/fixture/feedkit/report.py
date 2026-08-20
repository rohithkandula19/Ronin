"""Human-facing summaries of a single payload."""

from __future__ import annotations

from typing import Any

import feedkit.validate as validate


def render_summary(payload: dict[str, Any]) -> str:
    """One-line status for *payload*, suitable for a log."""
    problems = validate.check_feed_payload(payload)
    name = payload.get("id") or "<no id>"
    if not problems:
        return f"{name}: ok"
    return f"{name}: {len(problems)} problem(s): " + "; ".join(problems)
