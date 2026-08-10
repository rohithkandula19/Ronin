"""Rank rows by score.

Scores arrive from a CSV export, so a score may be a string of digits or an
int. Both mean the same number.
"""

from __future__ import annotations

from typing import Any


def top(rows: list[dict[str, Any]], limit: int) -> list[str]:
    ordered = sorted(rows, key=lambda row: int(row["score"]), reverse=True)
    return [row["name"] for row in ordered[:limit]]
