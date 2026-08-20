"""The inventory record."""

from __future__ import annotations

from dataclasses import dataclass

from invsync.mapping import canonical_sku


@dataclass(frozen=True, slots=True)
class Record:
    """One stock line. Frozen: a sync stage never edits its input."""

    sku: str
    name: str
    qty: int

    @property
    def key(self) -> str:
        """The case-insensitive identity of this line."""
        return canonical_sku(self.sku)
