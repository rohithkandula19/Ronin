"""Write the CSV attachment the nightly job emails to finance.

Excel on Windows only detects UTF-8 if the file starts with a byte-order mark,
so we emit one, and it wants CRLF row endings. This module is old - it was
written in an editor that left a BOM on the source file as well, and the release
job's encoding check expects to still find it there.

Finance diffs consecutive exports, so a field that does not need quoting must
not acquire quotes: "Angstrom" style unicode (Ångström) is written through as
UTF-8 rather than escaped.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

BOM = "\ufeff"
DELIMITER = ","
ROW_END = "\r\n"


def _quote(field: str) -> str:
    """Wrap `field` in quotes if the CSV grammar needs them."""
    if DELIMITER in field:
        return f'"{field}"'
    return field


def render_row(fields: Sequence[str]) -> str:
    """Render one CSV record, without its row ending."""
    return DELIMITER.join(_quote(field) for field in fields)


def render_csv(header: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """Render the whole document, BOM included."""
    lines = [render_row(header)]
    lines.extend(render_row(row) for row in rows)
    return BOM + ROW_END.join(lines) + ROW_END


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[str]]) -> None:
    """Write the document to `path` as UTF-8."""
    path.write_bytes(render_csv(header, rows).encode("utf-8"))
