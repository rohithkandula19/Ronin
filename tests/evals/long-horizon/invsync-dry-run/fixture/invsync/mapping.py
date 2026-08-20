"""Header aliases, because every supplier names the columns differently."""

from __future__ import annotations

HEADER_ALIASES = {
    "id": "sku",
    "sku": "sku",
    "title": "name",
    "name": "name",
    "stock": "qty",
    "quantity": "qty",
    "qty": "qty",
}


def canonical_header(name: str) -> str:
    """The internal field name for a supplier's column heading."""
    cleaned = name.strip().lower()
    return HEADER_ALIASES.get(cleaned, cleaned)
