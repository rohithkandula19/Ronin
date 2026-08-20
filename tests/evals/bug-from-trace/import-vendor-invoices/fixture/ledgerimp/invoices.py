"""Parses a vendor export into invoice records and totals them up."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from ledgerimp.formats import VENDOR_COLUMNS, money
from ledgerimp.reader import read_text


@dataclass(frozen=True)
class Invoice:
    vendor: str
    number: str
    amount: float

    def line(self) -> str:
        return f"{self.number}  {money(self.amount):>10}  {self.vendor}"


def parse_invoices(text: str) -> list[Invoice]:
    reader = csv.DictReader(io.StringIO(text))
    missing = [column for column in VENDOR_COLUMNS if column not in (reader.fieldnames or ())]
    if missing:
        raise ValueError(f"vendor export is missing column(s): {', '.join(missing)}")
    return [
        Invoice(
            vendor=row["vendor"].strip(),
            number=row["invoice"].strip(),
            amount=float(row["amount_eur"]),
        )
        for row in reader
    ]


def load_invoices(path: Path) -> list[Invoice]:
    return parse_invoices(read_text(path))


def report(invoices: list[Invoice]) -> str:
    lines = [invoice.line() for invoice in invoices]
    total = sum(invoice.amount for invoice in invoices)
    lines.append(f"{len(invoices)} invoice(s), total {money(total)} EUR")
    return "\n".join(lines) + "\n"
