"""Normalise contact records coming off the intake queue.

``middle`` is optional and arrives as JSON null when absent; a missing
middle name normalises to the empty string.
"""

from __future__ import annotations

from typing import Any


def full_name(record: dict[str, Any]) -> str:
    parts = [
        record["first"].strip(),
        record["middle"].strip(),
        record["last"].strip(),
    ]
    return " ".join(part for part in parts if part)
