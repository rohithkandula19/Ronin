"""Aggregate per-account usage records.

A record written before the metering rollout has no ``bytes`` field at all;
such a record contributes nothing to the total.
"""

from __future__ import annotations

from typing import Any


def total_bytes(records: list[dict[str, Any]]) -> int:
    return sum(record.get("bytes") for record in records)
