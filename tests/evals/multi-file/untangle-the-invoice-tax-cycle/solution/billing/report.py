"""Plain-text rendering of an invoice."""

from __future__ import annotations

from billing.invoice import Invoice
from billing.money import CURRENCY_SCALE, normalize_currency
from billing.tax import rate_for


def format_minor(amount_minor: int, currency: str) -> str:
    """Render integer minor units as a human amount, e.g. 5248 -> '52.48 USD'."""
    code = normalize_currency(currency)
    scale = CURRENCY_SCALE[code]
    if scale == 1:
        return f"{amount_minor} {code}"
    major, minor = divmod(amount_minor, scale)
    return f"{major}.{minor:02d} {code}"


def render(invoice: Invoice) -> str:
    """A one-line-per-line-item receipt."""
    totals = invoice.totals()
    code = totals["currency"]
    rows = [
        f"{line.quantity} x {line.description} "
        f"@ {rate_for(code, line.category)}bp"
        for line in invoice.lines
    ]
    rows.append(f"subtotal {format_minor(totals['subtotal_minor'], code)}")
    rows.append(f"tax {format_minor(totals['tax_minor'], code)}")
    rows.append(f"total {format_minor(totals['total_minor'], code)}")
    return "\n".join(rows)
