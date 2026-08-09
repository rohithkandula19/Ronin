"""Thin filesystem layer: read a vendor drop, write our own report."""

from __future__ import annotations

from pathlib import Path

from ledgerimp.formats import REPORT_ENCODING, VENDOR_ENCODING


def read_text(path: Path) -> str:
    """The whole vendor export as text."""
    return path.read_text(encoding=VENDOR_ENCODING)


def write_report(path: Path, text: str) -> None:
    path.write_text(text, encoding=REPORT_ENCODING)
