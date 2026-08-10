"""Emit one CSV record. Pure, so the streaming exporter can be tested."""

from __future__ import annotations

_SPECIAL = (",", '"', "\n", "\r")


def csv_row(fields: list[str]) -> str:
    """One RFC 4180 record, without the trailing newline."""
    out = []
    for field in fields:
        if any(ch in field for ch in _SPECIAL):
            out.append('"' + field.replace('"', '""') + '"')
        else:
            out.append(field)
    return ",".join(out)
