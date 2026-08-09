"""Turn a vendor invoice export into our ledger report.

Usage: python3 import_ledger.py [vendor_export.csv]
"""

from __future__ import annotations

import sys
from pathlib import Path

from ledgerimp.invoices import load_invoices, report


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else Path("vendor_export.csv")
    invoices = load_invoices(source)
    text = report(invoices)
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
