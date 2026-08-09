"""Money formatting helpers.

The pipeline keeps money as a *string* with exactly two decimal places: the feed
is compared byte for byte downstream, so ``19.9`` and ``19.90`` must not both be
possible.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

AMOUNT_RE = re.compile(r"^-?\d+\.\d{2}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def parse_amount(value: str) -> str:
    """Return ``value`` as a two-decimal string, or the input unchanged if unparsable.

    Unparsable values are left alone on purpose: the validator, not the transform,
    is the layer allowed to reject a row.
    """
    cleaned = value.replace(",", "").replace("_", "").strip()
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return value
    return f"{amount:.2f}"


def normalize_currency(value: str) -> str:
    """Upper-case a currency code and strip padding. Does not validate it."""
    return value.strip().upper()


def looks_like_currency(value: str) -> bool:
    return bool(CURRENCY_RE.match(value))
