"""Turn raw payloads into accepted/rejected batches."""

from __future__ import annotations

from typing import Any

from feedkit.validate import FeedError, collect_payload_problems


def ingest(payloads: list[dict[str, Any]], *, strict: bool = False) -> dict[str, Any]:
    """Split *payloads* into the ones we keep and the ones we drop."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for payload in payloads:
        problems = collect_payload_problems(payload)
        if problems:
            if strict:
                raise FeedError(f"{payload.get('id', '<no id>')}: {problems[0]}")
            rejected.append({"id": payload.get("id"), "problems": problems})
        else:
            accepted.append(payload)
    return {"accepted": accepted, "rejected": rejected}
