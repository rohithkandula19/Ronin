"""Repeated lines, which usually mean a bad copy-paste or a replayed log."""

from __future__ import annotations

from ..blob import Blob
from ..plugin import Plugin


class DuplicateLines(Plugin):
    name = "duplicate_lines"
    summary_line = "finds lines that appear more than once"
    declares = ("histogram", "warnings")

    def _counts(self, blob: Blob) -> dict[str, int]:
        counts: dict[str, int] = {}
        for line in blob.lines:
            stripped = line.strip()
            if not stripped:
                continue
            counts[stripped] = counts.get(stripped, 0) + 1
        return counts

    def histogram(self, blob: Blob) -> dict[str, int]:
        return {line: count for line, count in self._counts(blob).items() if count > 1}

    def warnings(self, blob: Blob) -> list[str]:
        repeats = self.histogram(blob)
        if not repeats:
            return []
        worst = sorted(repeats.items(), key=lambda item: (-item[1], item[0]))[0]
        return [f"{len(repeats)} line(s) repeat; worst is {worst[0][:40]!r} x{worst[1]}"]


PLUGIN = DuplicateLines()
