"""Bulk import from a CSV drop."""

from __future__ import annotations


def _looks_like_email(text: str) -> bool:
    local, _, domain = text.partition("@")
    return bool(local) and "." in domain


def import_rows(rows: list[str]) -> list[str]:
    return [row for row in rows if _looks_like_email(row)]
