"""CSV text into records. No csv module: the feeds are quote-free."""

from __future__ import annotations

from invsync.mapping import canonical_header
from invsync.records import Record


def parse_csv(text: str) -> list[Record]:
    """Records in file order. The first non-blank line is the header."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    headers = [canonical_header(cell) for cell in lines[0].split(",")]
    out = []
    for line in lines[1:]:
        cells = [cell.strip() for cell in line.split(",")]
        row = dict(zip(headers, cells))
        out.append(Record(sku=row["sku"], name=row["name"], qty=int(row["qty"])))
    return out
