"""Reader protocol plus the column projection every foreign-format reader needs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Mapping

RawRow = dict[str, str]


class Reader(ABC):
    """Yields ``(row_number, row)``; ``row`` keys are canonical field names."""

    #: Human-readable name used in error messages and the run summary.
    name = "reader"

    @abstractmethod
    def read(self) -> Iterator[tuple[int, RawRow]]:
        raise NotImplementedError


def rename_columns(row: Mapping[str, str], column_map: Mapping[str, str]) -> RawRow:
    """Project a source row onto field names.

    Source columns absent from ``column_map`` are dropped: shop exports carry a
    long tail of columns the feed does not want, and silently ignoring them is
    what keeps a new shop column from breaking a nightly run.
    """
    projected: RawRow = {}
    for source_name, value in row.items():
        field_name = column_map.get(source_name)
        if field_name is None:
            continue
        projected[field_name] = "" if value is None else str(value)
    return projected
