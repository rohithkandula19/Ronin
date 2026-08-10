"""Emit one CSV record. Pure, so the streaming exporter can be tested."""

from __future__ import annotations


def csv_row(fields: list[str]) -> str:
    """One RFC 4180 record, without the trailing newline."""
    return ",".join(fields)
