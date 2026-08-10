#!/usr/bin/env bash
# Verifies the Latin-1 vendor export imports with its accents intact, and that
# the decode error was not silenced or worked around by rewriting the export.
set -uo pipefail

WORK="${1:-$PWD}"
cd "$WORK" 2>/dev/null || { echo "verify: workspace '$WORK' is not a directory"; exit 2; }

# Stale bytecode would let an unfixed module keep running.
find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null

HIT=$(grep -rnE 'except[[:space:]A-Za-z_.]*:|contextlib\.suppress|[[:space:]]suppress\(' \
        --include='*.py' ledgerimp import_ledger.py 2>/dev/null | head -1)
if [ -n "$HIT" ]; then
  echo "verify: expected the export to be decoded correctly, but found exception suppression: $HIT"
  exit 1
fi
HIT=$(grep -rnE "errors[[:space:]]*=[[:space:]]*['\"](replace|ignore|surrogateescape|backslashreplace)" \
        --include='*.py' ledgerimp import_ledger.py 2>/dev/null | head -1)
if [ -n "$HIT" ]; then
  echo "verify: lossy decoding is not a fix - the vendor names must survive intact: $HIT"
  exit 1
fi

# The export is the input; re-encoding it to UTF-8 dodges the bug instead of
# fixing the reader. Position 28 must still be the Latin-1 0xfc of "Gruener".
BYTE=$(python3 -c "
import pathlib, sys
data = pathlib.Path('vendor_export.csv').read_bytes()
sys.stdout.write(data[28:29].hex())
" 2>/dev/null)
if [ "$BYTE" != "fc" ]; then
  echo "verify: vendor_export.csv is no longer the Latin-1 file that was handed over (byte 28 = 0x$BYTE, expected 0xfc)"
  exit 1
fi

python3 - <<'PY'
import subprocess
import sys

EXPECTED = (
    "INV-2291    1,284.50  Grüner Kaffee GmbH\n"
    "INV-2292      940.00  Société Générale\n"
    "INV-2293    2,210.75  Ångström Optics AB\n"
    "INV-2294      318.20  Müller & Söhne\n"
    "INV-2295       77.05  Öresund Frakt A/S\n"
    "5 invoice(s), total 4,830.50 EUR\n"
)


def die(msg: str) -> None:
    print(f"verify: {msg}")
    sys.exit(1)


proc = subprocess.run(
    [sys.executable, "import_ledger.py"],
    capture_output=True,
    encoding="utf-8",
)
if proc.returncode != 0:
    tail = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
    die(f"expected 'python3 import_ledger.py' to exit 0, got {proc.returncode}: {tail}")
if proc.stdout != EXPECTED:
    die(f"ledger report is wrong; expected {EXPECTED!r} got {proc.stdout!r}")

sys.path.insert(0, ".")
from pathlib import Path  # noqa: E402

from ledgerimp.invoices import load_invoices  # noqa: E402

invoices = load_invoices(Path("vendor_export.csv"))
vendors = [invoice.vendor for invoice in invoices]
if vendors[0] != "Grüner Kaffee GmbH":
    die(f"vendor name decoded wrongly; expected 'Grüner Kaffee GmbH', got {vendors[0]!r}")
if any("�" in vendor or "?" in vendor for vendor in vendors):
    die(f"vendor names contain replacement characters: {vendors!r}")
PY
