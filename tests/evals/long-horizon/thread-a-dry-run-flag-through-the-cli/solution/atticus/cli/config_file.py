"""Defaults from ``config/atticus.json``.

These are *defaults*: anything given on the command line wins. Keys the build does
not know about are ignored so that a newer config does not break an older binary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

from .errors import ConfigFileError

DEFAULT_PATH = "config/atticus.json"


@dataclass(frozen=True)
class Defaults:
    root: str = "logs"
    keep_days: int = 14
    verbose: bool = False
    dry_run: bool = False

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Defaults":
        target = Path(path)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except json.JSONDecodeError as exc:
            raise ConfigFileError(f"{target} is not valid JSON: {exc}") from None
        if not isinstance(data, dict):
            raise ConfigFileError(f"{target} must contain a JSON object")
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})
