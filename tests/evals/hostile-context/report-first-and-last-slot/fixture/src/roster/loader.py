"""Read a day's raw roster entries."""

from __future__ import annotations

import json
from pathlib import Path


def load_raw(path):
    """Return the raw entry list for one day.

    The scheduling app writes these files; we take them as they come and do the
    interpreting in :mod:`roster.slots`.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data["entries"]
    if not isinstance(entries, list):
        raise ValueError(f"{path}: 'entries' must be a list")
    return entries
