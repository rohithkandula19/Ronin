#!/usr/bin/env bash
# Verifier for add-feature/render-the-invoice-as-csv.
#
# Checks the observable behaviour the prompt asked for and nothing about the shape
# of the implementation, so a different-but-correct answer passes.
set -uo pipefail

TASK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="${1:-${RONIN_EVAL_WORKSPACE:-$PWD}}"

if [ ! -d "$WS" ]; then
  echo "verify(add-feature/render-the-invoice-as-csv): TASK SETUP ERROR: workspace '$WS' is not a directory"
  exit 2
fi
cd "$WS" || exit 2

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "verify(add-feature/render-the-invoice-as-csv): TASK SETUP ERROR: no python interpreter on PATH"
  exit 2
fi

find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

PYTHONPATH="$PWD" "$PY" - <<'RONIN_VERIFY_EOF'
import sys

SLUG = "render-the-invoice-as-csv"


def fail(msg):
    print(f"verify(add-feature/{SLUG}): {msg}")
    raise SystemExit(1)


def close(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False

import csv
import io

try:
    from billing.invoice import Invoice, LineItem, render_text
except Exception as exc:
    fail(f"could not import billing.invoice: {type(exc).__name__}: {exc}")
try:
    from billing.invoice import render_csv
except ImportError as exc:
    fail(f"billing.invoice has no render_csv: {exc}")

ITEMS = (
    LineItem("Bolts", 3, 19.99),
    LineItem("Widget, large", 2, 12.5),
    LineItem("Grommets", 10, 0.5),
)
INVOICE = Invoice(number="2024-014", customer="Acme Ltd", items=ITEMS)
HEADER = ["description", "quantity", "unit_price", "amount"]

try:
    rendered = render_csv(INVOICE)
except Exception as exc:
    fail(f"render_csv(invoice) raised {type(exc).__name__}: {exc}")
if not isinstance(rendered, str):
    fail(f"render_csv should return a string, got a {type(rendered).__name__}")

rows = [row for row in csv.reader(io.StringIO(rendered)) if row]
if not rows:
    fail("render_csv returned nothing that csv.reader could read")
if rows[0] != HEADER:
    fail(f"the first row should be the header {HEADER}, got {rows[0]}")
if len(rows) != len(ITEMS) + 2:
    fail(
        f"expected {len(ITEMS) + 2} rows (header, {len(ITEMS)} line items, total), got "
        f"{len(rows)}: {rows}"
    )

for item, row in zip(ITEMS, rows[1:-1]):
    if len(row) != 4:
        fail(f"line item rows need 4 columns, got {len(row)}: {row}")
    if row[0] != item.description:
        fail(f"description column should be {item.description!r} (quoted if it has a comma), got {row[0]!r}")
    if not close(row[1], item.quantity):
        fail(f"quantity column for {item.description!r} should be {item.quantity}, got {row[1]!r}")
    if row[2] != f"{item.unit_price:.2f}":
        fail(f"unit_price for {item.description!r} should be {f'{item.unit_price:.2f}'!r}, got {row[2]!r}")
    if row[3] != f"{item.amount:.2f}":
        fail(f"amount for {item.description!r} should be {f'{item.amount:.2f}'!r}, got {row[3]!r}")

total_row = rows[-1]
if len(total_row) != 4:
    fail(f"the total row needs 4 columns, got {len(total_row)}: {total_row}")
if total_row[0] != "TOTAL":
    fail(f"the last row's description should be 'TOTAL', got {total_row[0]!r}")
if total_row[1] != "" or total_row[2] != "":
    fail(f"the total row's quantity and unit_price must be empty, got {total_row[1]!r} and {total_row[2]!r}")
if total_row[3] != f"{INVOICE.total:.2f}":
    fail(f"the total row's amount should be {f'{INVOICE.total:.2f}'!r}, got {total_row[3]!r}")

# a one-line invoice, exactly as spelled out in the request
single = Invoice(number="2024-015", customer="Acme Ltd", items=(LineItem("Bolts", 3, 19.99),))
single_rows = [row for row in csv.reader(io.StringIO(render_csv(single))) if row]
if single_rows != [HEADER, ["Bolts", "3", "19.99", "59.97"], ["TOTAL", "", "", "59.97"]]:
    fail(f"the single-line example from the request came out as {single_rows}")

# render_text must still behave
text = render_text(INVOICE)
if not text.endswith("\n") or text.splitlines()[0] != "Invoice 2024-014 for Acme Ltd":
    fail(f"render_text changed: it starts {text.splitlines()[:1]!r} and endswith-newline={text.endswith(chr(10))}")
for item in ITEMS:
    if item.description not in text:
        fail(f"render_text no longer mentions the line item {item.description!r}")
if f"{INVOICE.total:.2f}" not in text or "TOTAL" not in text:
    fail("render_text no longer shows the TOTAL line")

RONIN_VERIFY_EOF
