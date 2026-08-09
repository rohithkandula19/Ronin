"""Read raw catalog records off disk."""

from __future__ import annotations

import json
from pathlib import Path


def load_records(path):
    """Return the raw record list stored at *path*.

    The file is a JSON array of objects. No normalisation happens here; the
    index is responsible for deciding which records are usable.
    """
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array of records")
    return data
