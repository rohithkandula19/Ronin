"""Bulk import from a CSV drop."""

from __future__ import annotations

from signup.validate import is_valid_address


def import_rows(rows: list[str]) -> list[str]:
    return [row for row in rows if is_valid_address(row)]
