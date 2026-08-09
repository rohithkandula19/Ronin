"""Reader for the nightly retail CSV export."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from ..errors import ReaderError
from .base import RawRow, Reader, rename_columns


class CsvOrderReader(Reader):
    """Reads the retail export, whose column names are not ours.

    ``COLUMN_MAP`` maps *source column* -> *field name*. Columns missing from the
    map never reach the rest of the pipeline.
    """

    name = "csv"

    COLUMN_MAP = {
        "order_id": "order_id",
        "cust": "customer_id",
        "channel": "channel",
        "amount_gross": "amount",
        "qty": "quantity",
        "region": "region",
    }

    def __init__(self, path: str | Path, delimiter: str = ",", encoding: str = "utf-8") -> None:
        self.path = Path(path)
        self.delimiter = delimiter
        self.encoding = encoding

    def read(self) -> Iterator[tuple[int, RawRow]]:
        try:
            handle = self.path.open("r", encoding=self.encoding, newline="")
        except FileNotFoundError as exc:
            raise ReaderError(f"source file not found: {self.path}") from exc
        with handle:
            reader = csv.DictReader(handle, delimiter=self.delimiter)
            header = tuple(reader.fieldnames or ())
            if not header:
                raise ReaderError(f"source file {self.path} has no header row")
            missing = [column for column in self.COLUMN_MAP if column not in header]
            if missing:
                raise ReaderError(
                    f"source file {self.path} is missing mapped columns {missing}; "
                    f"header is {list(header)}"
                )
            # Row 1 is the header, so data rows start at 2 and line numbers in an
            # error message match what an operator sees in a text editor.
            for row_number, row in enumerate(reader, start=2):
                yield row_number, rename_columns(row, self.COLUMN_MAP)
