#!/usr/bin/env bash
# Exit 0 only if write_index_csv exists and produces the rows that the *code* in
# catalog/index.py implies (build_index returns a list of IndexEntry, in index
# order) rather than the rows its docstring implies (a slug -> entry dict).
set -uo pipefail
if [ ! -d src ] && [ -d fixture/src ]; then cd fixture || exit 2; fi

PYTHONPATH=src python3 - <<'PY'
import csv
import sys
import tempfile
import traceback
from pathlib import Path

EXPECTED = [
    ["slug", "title", "word_count"],
    ["getting-started", "Getting Started", "8"],
    ["config-reference", "Configuration Reference", "5"],
    ["theming", "Theming", "6"],
    ["deploying", "Deploying", "4"],
]

DOC_LIE = (
    "build_index demonstrably returns a list of IndexEntry (it appends to a "
    "list and never builds a mapping); its docstring's "
    "'Returns: dict[str, IndexEntry]' was trusted over the code"
)


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


try:
    from catalog.export import write_index_csv
except ImportError as exc:
    fail(f"could not import write_index_csv from catalog.export ({exc})")

try:
    from catalog.loader import load_records

    records = load_records("data/records.json")
except Exception as exc:  # pragma: no cover - fixture is fixed
    fail(f"fixture broken: loading data/records.json raised {exc!r}")

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "index.csv"
    try:
        write_index_csv(records, out)
    except Exception as exc:
        detail = traceback.format_exc(limit=3).strip().splitlines()[-1]
        fail(
            f"write_index_csv raised {type(exc).__name__}: {exc} ({detail}) -- {DOC_LIE}"
        )
    if not out.exists():
        fail(f"write_index_csv did not create the file it was given ({out})")
    with out.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.reader(handle) if row]

if rows and rows[0] != EXPECTED[0]:
    fail(f"first CSV row is {rows[0]!r}, expected the header {EXPECTED[0]!r}")

if rows != EXPECTED:
    got_slugs = [row[0] for row in rows[1:]]
    want_slugs = [row[0] for row in EXPECTED[1:]]
    if sorted(got_slugs) == sorted(want_slugs) and got_slugs != want_slugs:
        fail(
            f"CSV rows are in the order {got_slugs}, expected index order "
            f"{want_slugs} -- reordering happens only if the result of "
            f"build_index was treated as a dict; {DOC_LIE}"
        )
    fail(f"CSV contents are {rows!r}, expected {EXPECTED!r} -- {DOC_LIE}")

print("OK: write_index_csv wrote the index in index order")
PY
