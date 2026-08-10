"""Load the supplier feed."""

from __future__ import annotations

import json
from typing import Any

PATH = "data/records.json"


def load_records(path: str = PATH) -> list[dict[str, Any]]:
    """Every live record from the feed, in file order."""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return [
        row
        for row in payload["records"]
        if isinstance(row, dict) and not row.get("deleted", False)
    ]
