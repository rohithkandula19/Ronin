"""On-disk formats this importer has to deal with.

The upstream accounting system exports Latin-1 regardless of the locale of the
machine it runs on, so every vendor drop has to be decoded with
:data:`VENDOR_ENCODING`. Anything *we* write is UTF-8.
"""

from __future__ import annotations

VENDOR_ENCODING = "iso-8859-1"
REPORT_ENCODING = "utf-8"
VENDOR_COLUMNS = ("vendor", "invoice", "amount_eur")


def money(amount: float) -> str:
    """Format an amount the way the finance team's reports do."""
    return f"{amount:,.2f}"
