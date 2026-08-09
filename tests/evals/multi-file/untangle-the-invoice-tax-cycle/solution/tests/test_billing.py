import unittest

from billing import Invoice, Line
from billing.money import CURRENCY_SCALE, normalize_currency, to_minor_units
from billing.report import format_minor, render
from billing.tax import rate_for, tax_for_line

INVOICE = Invoice(
    currency="us$",
    lines=(
        Line("Paperback", 19.99, 2, "books"),
        Line("Tote bag", 12.50, 1, "merch"),
    ),
)


class CurrencyTests(unittest.TestCase):
    def test_aliases_are_normalized(self):
        self.assertEqual(normalize_currency("us$"), "USD")
        self.assertEqual(normalize_currency(" euro "), "EUR")

    def test_an_unknown_currency_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_currency("XTS")

    def test_yen_has_no_minor_unit(self):
        self.assertEqual(CURRENCY_SCALE["JPY"], 1)
        self.assertEqual(CURRENCY_SCALE["USD"], 100)

    def test_minor_units_respect_the_scale(self):
        self.assertEqual(to_minor_units(19.99, "USD"), 1999)
        self.assertEqual(to_minor_units(1500, "JPY"), 1500)


class TaxTests(unittest.TestCase):
    def test_a_category_rate_wins_over_the_default(self):
        self.assertEqual(rate_for("USD", "books"), 700)
        self.assertEqual(rate_for("USD", "merch"), 875)

    def test_tax_is_floored_to_the_minor_unit(self):
        self.assertEqual(tax_for_line(1250, "USD", "merch"), 109)


class InvoiceTests(unittest.TestCase):
    def test_totals_are_exact_integers(self):
        self.assertEqual(
            INVOICE.totals(),
            {
                "currency": "USD",
                "subtotal_minor": 5248,
                "tax_minor": 388,
                "total_minor": 5636,
            },
        )


class ReportTests(unittest.TestCase):
    def test_format_minor_splits_on_the_scale(self):
        self.assertEqual(format_minor(5248, "USD"), "52.48 USD")
        self.assertEqual(format_minor(1500, "JPY"), "1500 JPY")

    def test_render_ends_with_the_total(self):
        self.assertEqual(render(INVOICE).splitlines()[-1], "total 56.36 USD")


if __name__ == "__main__":
    unittest.main()
