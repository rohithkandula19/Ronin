"""The inventory record."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Record:
    """One stock line. Frozen: a sync stage never edits its input."""

    sku: str
    name: str
    qty: int
