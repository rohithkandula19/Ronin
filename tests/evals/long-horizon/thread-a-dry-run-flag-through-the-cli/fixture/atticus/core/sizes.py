"""Sizes, rendered the way the report wants them."""

from __future__ import annotations

from .scan import LogFile

UNITS = ("B", "KiB", "MiB", "GiB")


def human_size(count: int) -> str:
    size = float(count)
    for unit in UNITS:
        if size < 1024 or unit == UNITS[-1]:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def total_size(files: tuple[LogFile, ...]) -> int:
    return sum(f.size for f in files)
