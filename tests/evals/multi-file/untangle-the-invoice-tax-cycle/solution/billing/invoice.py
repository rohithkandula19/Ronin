"""Invoices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from billing.money import normalize_currency, to_minor_units
from billing.tax import tax_for_line


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
