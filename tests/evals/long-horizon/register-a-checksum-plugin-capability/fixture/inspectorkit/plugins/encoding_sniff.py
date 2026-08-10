"""Guess an encoding and complain about the awkward cases."""

from __future__ import annotations

from ..blob import Blob
from ..plugin import Plugin

BOMS = {
    b"\xef\xbb\xbf": "utf-8-sig",
    b"\xff\xfe": "utf-16-le",
    b"\xfe\xff": "utf-16-be",
}


class EncodingSniff(Plugin):
    name = "encoding_sniff"
    summary_line = "guesses the encoding and flags BOMs and undecodable bytes"
    declares = ("summary", "warnings")

    def _bom(self, data: bytes) -> str | None:
        for marker, label in BOMS.items():
            if data.startswith(marker):
                return label
        return None

    def _decodes_as_utf8(self, data: bytes) -> bool:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True

    def summarize(self, blob: Blob) -> dict[str, str]:
        bom = self._bom(blob.data)
        return {
            "bom": bom or "none",
            "utf8_clean": "yes" if self._decodes_as_utf8(blob.data) else "no",
        }

    def warnings(self, blob: Blob) -> list[str]:
        found: list[str] = []
        bom = self._bom(blob.data)
        if bom is not None:
            found.append(f"file starts with a {bom} byte-order mark")
        if not self._decodes_as_utf8(blob.data):
            found.append("file is not valid UTF-8; text output will contain U+FFFD")
        return found


PLUGIN = EncodingSniff()
