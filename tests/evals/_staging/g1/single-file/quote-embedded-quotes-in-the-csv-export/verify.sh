#!/usr/bin/env bash
# Workspace root: first argument if given, otherwise the current directory.
set -u
ROOT="${1:-$PWD}"
PY="${PYTHON:-python3}"
cd "$ROOT" || { echo "verify: workspace root '$ROOT' does not exist"; exit 2; }

"$PY" - <<'PYCODE'
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, "src")

SOURCE = Path("src/exports/csv_writer.py")
OUT = Path(".verify-export.csv")


def fail(message: str) -> None:
    OUT.unlink(missing_ok=True)
    print(f"verify: {message}")
    raise SystemExit(1)


if not SOURCE.exists():
    fail(f"{SOURCE} is missing")

# 1. The source file itself carries a UTF-8 BOM and non-ASCII text; both must survive.
raw = SOURCE.read_bytes()
if not raw.startswith(b"\xef\xbb\xbf"):
    fail(f"{SOURCE} lost its UTF-8 BOM: it now starts with {raw[:4]!r}")
text = raw.decode("utf-8-sig")
if "Ångström" not in text:
    fail(f"{SOURCE} lost the non-ASCII text in its docstring - it was re-encoded, not edited")

try:
    from exports.csv_writer import render_csv, render_row, write_csv
except Exception as exc:  # noqa: BLE001
    fail(f"could not import exports.csv_writer: {exc!r}")

# 2. Fields containing quotes, delimiters and newlines round-trip through a real parser.
header = ["item", "note", "amount"]
rows = [
    ["Coffee, ground", 'He said "hi"', "12.50"],
    ["Cable 3\" jack", 'a "quoted, thing"', "4.00"],
    ["Ångström ruler", "line1\nline2", "9.99"],
    ["plain", "nothing special", "1.00"],
]
document = render_csv(header, rows)
if not document.startswith("\ufeff"):
    fail(f"the rendered document lost its leading BOM: it starts {document[:6]!r}")
parsed = list(csv.reader(io.StringIO(document[1:])))
if parsed != [header, *rows]:
    fail(f"CSV did not round-trip: expected {[header, *rows]!r}, parsed back {parsed!r}")

# 3. Fields that need no quoting must not acquire any - finance diffs the exports.
got = render_row(["plain", "nothing special", "1.00"])
if got != "plain,nothing special,1.00":
    fail(f"expected render_row to leave harmless fields unquoted, got {got!r}")
got = render_row(["Coffee, ground"])
if got != '"Coffee, ground"':
    fail(f"expected a field containing a comma to stay quoted, got {got!r}")

# 4. Row endings are still CRLF.
plain = render_csv(header, [["a", "b", "c"], ["d", "e", "f"]])
lf = plain.count("\n")
crlf = plain.count("\r\n")
if lf != crlf or crlf != 3:
    fail(f"expected 3 CRLF row endings and no bare LF, got {crlf} CRLF and {lf} line feeds")

# 5. The file on disk starts with the BOM Excel needs.
write_csv(OUT, header, rows)
written = OUT.read_bytes()
if not written.startswith(b"\xef\xbb\xbf"):
    fail(f"the written CSV does not start with a UTF-8 BOM: {written[:6]!r}")
if "Ångström ruler".encode() not in written:
    fail("the written CSV is not UTF-8 encoded any more")
OUT.unlink(missing_ok=True)
PYCODE
