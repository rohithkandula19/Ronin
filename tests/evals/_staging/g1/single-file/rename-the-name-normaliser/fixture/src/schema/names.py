"""Column- and path-name handling for the spreadsheet importer.

Uploaded sheets arrive with whatever the customer typed in row 1, so every
header cell is folded to a stable snake_case identifier before it reaches the
warehouse, and folded back for display.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from pathlib import PurePosixPath

__all__ = ["denormalize", "header_map", "normalize", "normalize_path"]

_NON_WORD = re.compile(r"[^0-9a-z]+")


def normalize(name: str) -> str:
    """Fold a header cell to the snake_case identifier we store the column under."""
    # NFKD then ASCII-fold: "Größe (m²)" and "Grosse (m2)" must not land in
    # separate columns just because one sheet used the ligature form.
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return _NON_WORD.sub("_", folded.strip().lower()).strip("_")


def denormalize(name: str) -> str:
    """Turn a stored column name back into something a human reads."""
    return " ".join(word.capitalize() for word in name.split("_") if word)


def normalize_path(path: str) -> str:
    """Clean up a sheet path: drop `.` segments and any trailing slash."""
    pure = PurePosixPath(path)
    parts = [part for part in pure.parts if part not in (".", "")]
    return "/".join(parts)


def header_map(header: Sequence[str]) -> dict[str, str]:
    """Map each raw header cell to the column name it is stored as.

    Later duplicates get a numeric suffix so two "Notes" columns stay distinct.
    """
    mapping: dict[str, str] = {}
    seen: dict[str, int] = {}
    for cell in header:
        column = normalize(cell)
        count = seen.get(column, 0)
        seen[column] = count + 1
        mapping[cell] = column if count == 0 else f"{column}_{count + 1}"
    return mapping
