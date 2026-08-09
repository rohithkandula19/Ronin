"""Byte-level facts about a file: size, printable ratio, stray control bytes."""

from __future__ import annotations

from ..blob import Blob
from ..plugin import Plugin
from ..util.fmt import human_size
from ..util.io import fingerprint

PRINTABLE = frozenset(bytes(range(32, 127)) + b"\t\n\r")


class BytesStats(Plugin):
    name = "bytes_stats"
    summary_line = "size, printable ratio and control-byte counts"
    declares = ("summary", "checksum")

    def summarize(self, blob: Blob) -> dict[str, str]:
        data = blob.data
        printable = sum(1 for byte in data if byte in PRINTABLE)
        ratio = 1.0 if not data else printable / len(data)
        return {
            "bytes": str(len(data)),
            "size": human_size(len(data)),
            "printable_ratio": f"{ratio:.3f}",
            "nul_bytes": str(data.count(b"\x00")),
            "crlf_line_endings": "yes" if b"\r\n" in data else "no",
        }

    def checksum(self, blob: Blob) -> str:
        # Same format the tree scanner prints, so the two outputs can be compared.
        return fingerprint(blob.data)


PLUGIN = BytesStats()
