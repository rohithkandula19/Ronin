"""Tax rates, in basis points so the arithmetic stays exact."""

from __future__ import annotations

RATES_BP: dict[tuple[str, str], int] = {
    ("USD", "books"): 700,
    ("USD", "default"): 875,
    ("EUR", "books"): 550,
    ("EUR", "default"): 2100,
    ("GBP", "default"): 2000,
    ("JPY", "default"): 1000,
}


def rate_for(currency: str, category: str = "default") -> int:
    """Basis points of tax for *currency*/*category*, falling back per currency."""
    # Imported here rather than at module scope: billing.invoice imports us, so a
    # top-level import of it blows up with a circular import.
    from billing.invoice import normalize_currency

    code = normalize_currency(currency)
    if (code, category) in RATES_BP:
        return RATES_BP[(code, category)]
    return RATES_BP.get((code, "default"), 0)


def tax_for_line(amount_minor: int, currency: str, category: str = "default") -> int:
    """Tax owed on *amount_minor*, rounded down to the minor unit."""
    return amount_minor * rate_for(currency, category) // 10_000
