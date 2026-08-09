"""The canonical feed writer.

Column order is pinned rather than taken from the record: downstream jobs diff
successive feeds, so two runs of the same data must produce byte-identical lines.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, TextIO

from ..errors import WriterError


class JsonlWriter:
    name = "jsonl"

    #: Order of keys in every emitted line.
    COLUMN_ORDER: tuple[str, ...] = (
        "order_id",
        "customer_id",
        "channel",
        "amount",
        "currency",
        "quantity",
        "region",
    )

    def __init__(self, path: str | Path, encoding: str = "utf-8") -> None:
        self.path = Path(path)
        self.encoding = encoding
        self._handle: TextIO | None = None
        self.written = 0

    def __enter__(self) -> "JsonlWriter":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._handle = self.path.open("w", encoding=self.encoding, newline="\n")
        except OSError as exc:
            raise WriterError(f"cannot open {self.path} for writing: {exc}") from exc

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def write(self, record: Mapping[str, str]) -> None:
        if self._handle is None:
            raise WriterError("writer used before open(); use it as a context manager")
        line = {name: record[name] for name in self.COLUMN_ORDER if name in record}
        self._handle.write(json.dumps(line, ensure_ascii=False, sort_keys=False) + "\n")
        self.written += 1
