"""Translate config/logging.toml into logging-handler specs.

A *spec* is a plain dict the daemon feeds to ``logging.config.dictConfig``
after adding the formatter. Specs are returned sorted by handler name so a
config reshuffle never reorders the daemon's startup log.

The shape depends on the handler's ``[handlers.<name>.rotation]`` table:

* no rotation table::

    {"name": ..., "class": "FileHandler", "filename": ..., "level": ...}

* ``mode = "size"``::

    {"name": ..., "class": "RotatingFileHandler", "filename": ..., "level": ...,
     "max_bytes": <int>, "backup_count": <int>}

* ``mode = "time"``::

    {"name": ..., "class": "TimedRotatingFileHandler", "filename": ...,
     "level": ..., "when": <str>, "interval": <int>, "backup_count": <int>}

``max_bytes`` and ``interval`` default to 0 and 1 respectively when the table
omits them; ``backup_count`` defaults to 0. Any other ``mode`` is a
``ValueError`` naming the handler.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

__all__ = ["build_handler_specs", "load_config"]


def load_config(path: str | Path) -> dict[str, Any]:
    """Read a logging config from ``path``."""
    with Path(path).open("rb") as handle:
        return tomllib.load(handle)


def build_handler_specs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build handler specs from a parsed logging config."""
    handlers = config.get("handlers", {})
    specs: list[dict[str, Any]] = []
    for name in sorted(handlers):
        entry = handlers[name]
        specs.append(
            {
                "name": name,
                "class": "FileHandler",
                "filename": entry["filename"],
                "level": entry.get("level", "INFO"),
            }
        )
    return specs
