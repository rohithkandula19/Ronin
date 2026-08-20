"""Normalise contact records coming off the intake queue.

``middle`` is optional and arrives as JSON null when absent; a missing
middle name normalises to the empty string.
"""

from __future__ import annotations

from typing import Any


def _clean(value: str | None) -> str:
    return value.strip() if value else ""


def full_name(record: dict[str, Any]) -> str:
    parts = [
        _clean(record["first"]),
        _clean(record.get("middle")),
        _clean(record["last"]),
    ]
    return " ".join(part for part in parts if part)
