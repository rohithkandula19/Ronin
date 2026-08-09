"""The invoice model and its plain-text rendering."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass


@dataclass(frozen=True)
class LineItem:
    description: str
    quantity: int
    unit_price: float

    @property
    def amount(self):
        """What this line costs, rounded to the cent."""
        return round(self.quantity * self.unit_price, 2)


@dataclass(frozen=True)
class Invoice:
    number: str
    customer: str
    items: tuple

    @property
    def total(self):
        """The sum of the line amounts, rounded to the cent."""
        return round(sum(item.amount for item in self.items), 2)


def render_text(invoice):
    """Render *invoice* as the fixed-width text we email out."""
    lines = [f"Invoice {invoice.number} for {invoice.customer}", ""]
    for item in invoice.items:
        lines.append(
            f"{item.description:<24}{item.quantity:>4} x {item.unit_price:>9.2f}"
            f" = {item.amount:>10.2f}"
        )
    lines.append("")
    lines.append(f"{'TOTAL':<24}{invoice.total:>28.2f}")
    return "\n".join(lines) + "\n"


def render_csv(invoice):
    """Render *invoice* as CSV for the spreadsheet finance keeps.

    The csv module does the quoting: a description with a comma in it would
    otherwise silently split into two columns.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["description", "quantity", "unit_price", "amount"])
    for item in invoice.items:
        writer.writerow(
            [item.description, item.quantity, f"{item.unit_price:.2f}", f"{item.amount:.2f}"]
        )
    writer.writerow(["TOTAL", "", "", f"{invoice.total:.2f}"])
    return buffer.getvalue()
