"""Line shape of a text file."""

from __future__ import annotations

from ..blob import Blob
from ..plugin import Plugin


class LineCount(Plugin):
    name = "line_count"
    summary_line = "counts lines, blank lines and the longest line"
    declares = ("summary",)

    def summarize(self, blob: Blob) -> dict[str, str]:
        lines = blob.lines
        blank = sum(1 for line in lines if not line.strip())
        longest = max((len(line) for line in lines), default=0)
        return {
            "lines": str(len(lines)),
            "blank_lines": str(blank),
            "longest_line": str(longest),
            "ends_with_newline": "yes" if blob.data.endswith(b"\n") else "no",
        }


PLUGIN = LineCount()
