"""Currency codes and minor-unit arithmetic.

This module deliberately depends on nothing else in `billing`, so both
`billing.invoice` and `billing.tax` can import it at module scope.
"""

from __future__ import annotations

# Minor units per major unit. JPY has none.
CURRENCY_SCALE: dict[str, int] = {"USD": 100, "EUR": 100, "GBP": 100, "JPY": 1}

CURRENCY_ALIASES: dict[str, str] = {"US$": "USD", "$": "USD", "EURO": "EUR", "": "USD"}


def normalize_currency(code: str) -> str:
    """Return the ISO code for *code*, accepting the sloppy spellings we see."""
    cleaned = code.strip().upper()
    cleaned = CURRENCY_ALIASES.get(cleaned, cleaned)
    if cleaned not in CURRENCY_SCALE:
        raise ValueError(f"unsupported currency: {code!r}")
    return cleaned


def to_minor_units(amount: float, currency: str) -> int:
    """Convert a major-unit amount to integer minor units."""
    return int(round(amount * CURRENCY_SCALE[normalize_currency(currency)]))
