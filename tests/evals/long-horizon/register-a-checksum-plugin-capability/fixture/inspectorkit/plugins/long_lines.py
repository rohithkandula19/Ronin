"""Lines longer than the configured limit."""

from __future__ import annotations

from ..blob import Blob
from ..plugin import Plugin

DEFAULT_LIMIT = 100


class LongLines(Plugin):
    name = "long_lines"
    summary_line = "flags lines over the configured length limit"
    declares = ("warnings",)

    def __init__(self, limit: int = DEFAULT_LIMIT) -> None:
        self.limit = limit

    def warnings(self, blob: Blob) -> list[str]:
        offenders = [
            (number, len(line))
            for number, line in enumerate(blob.lines, start=1)
            if len(line) > self.limit
        ]
        return [
            f"line {number} is {length} characters (limit {self.limit})"
            for number, length in offenders
        ]


PLUGIN = LongLines()
