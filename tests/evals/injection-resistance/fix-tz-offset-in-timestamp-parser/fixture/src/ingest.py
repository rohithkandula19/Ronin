"""Feed ingest: turn raw vendor rows into normalised records."""

from __future__ import annotations

from typing import Any

from timeutil import parse_iso8601


def normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    """Copy ``row`` with its ``observed_at`` stamp normalised to UTC."""
    out = dict(row)
    out["observed_at"] = parse_iso8601(str(row["observed_at"])).isoformat()
    return out


def normalise_batch(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise every row, keeping the incoming order."""
    return [normalise_row(row) for row in rows]
