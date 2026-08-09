"""Reader that replays our own feed, e.g. to re-validate after a schema change."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from ..errors import ReaderError
from ..schema import FIELD_NAMES
from .base import RawRow, Reader


class JsonlFeedReader(Reader):
    """Reads canonical records, so it projects onto the schema instead of a map."""

    name = "jsonl"

    def __init__(self, path: str | Path, encoding: str = "utf-8") -> None:
        self.path = Path(path)
        self.encoding = encoding

    def read(self) -> Iterator[tuple[int, RawRow]]:
        try:
            text = self.path.read_text(encoding=self.encoding)
        except FileNotFoundError as exc:
            raise ReaderError(f"feed file not found: {self.path}") from exc
        for row_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReaderError(f"{self.path}:{row_number} is not valid JSON: {exc}") from exc
            row: RawRow = {}
            for name in FIELD_NAMES:
                if name in record:
                    row[name] = "" if record[name] is None else str(record[name])
            yield row_number, row
