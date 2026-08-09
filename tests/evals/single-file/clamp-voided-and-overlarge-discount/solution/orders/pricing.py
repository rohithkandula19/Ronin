"""Order line pricing, in integer cents so no float ever reaches an invoice."""


def line_total(unit_cents: int, quantity: int, discount_pct: int = 0) -> int:
    """Total for one order line, in cents.

    A quantity of zero or less means the line was voided, so it contributes
    nothing. The discount percentage is clamped to 0..100: a bad feed value
    must not turn a sale into a refund.
    """
    if quantity <= 0:
        return 0
    pct = min(max(discount_pct, 0), 100)
    subtotal = unit_cents * quantity
    return subtotal - subtotal * pct // 100
