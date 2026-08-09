"""Invoices, and the currency helpers the rest of the package leans on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from billing.tax import tax_for_line

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


@dataclass(frozen=True)
class Line:
    description: str
    unit_amount: float
    quantity: int = 1
    category: str = "default"


@dataclass(frozen=True)
class Invoice:
    currency: str
    lines: tuple[Line, ...] = ()

    def totals(self) -> dict[str, Any]:
        """Subtotal, tax and total, all in integer minor units."""
        currency = normalize_currency(self.currency)
        subtotal = 0
        tax = 0
        for line in self.lines:
            minor = to_minor_units(line.unit_amount, currency) * line.quantity
            subtotal += minor
            tax += tax_for_line(minor, currency, line.category)
        return {
            "currency": currency,
            "subtotal_minor": subtotal,
            "tax_minor": tax,
            "total_minor": subtotal + tax,
        }
