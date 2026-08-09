from billing.invoice import Invoice, LineItem, render_text

INVOICE = Invoice(
    number="2024-014",
    customer="Acme Ltd",
    items=(
        LineItem("Bolts", 3, 19.99),
        LineItem("Grommets", 10, 0.5),
    ),
)


def test_line_amount_is_quantity_times_price():
    assert LineItem("Bolts", 3, 19.99).amount == 59.97


def test_total_sums_the_lines():
    assert INVOICE.total == 64.97


def test_text_starts_with_the_invoice_header():
    first = render_text(INVOICE).splitlines()[0]
    assert first == "Invoice 2024-014 for Acme Ltd"


def test_text_ends_with_a_newline():
    assert render_text(INVOICE).endswith("\n")
