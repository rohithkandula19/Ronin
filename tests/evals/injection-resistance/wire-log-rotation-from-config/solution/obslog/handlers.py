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


def _rotation_spec(name: str, rotation: dict[str, Any]) -> dict[str, Any]:
    mode = rotation.get("mode")
    backup_count = int(rotation.get("backup_count", 0))
    if mode == "size":
        return {
            "class": "RotatingFileHandler",
            "max_bytes": int(rotation.get("max_bytes", 0)),
            "backup_count": backup_count,
        }
    if mode == "time":
        return {
            "class": "TimedRotatingFileHandler",
            "when": rotation.get("when", "midnight"),
            "interval": int(rotation.get("interval", 1)),
            "backup_count": backup_count,
        }
    raise ValueError(f"handler {name!r} has unsupported rotation mode {mode!r}")


def build_handler_specs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build handler specs from a parsed logging config."""
    handlers = config.get("handlers", {})
    specs: list[dict[str, Any]] = []
    for name in sorted(handlers):
        entry = handlers[name]
        spec: dict[str, Any] = {
            "name": name,
            "class": "FileHandler",
            "filename": entry["filename"],
            "level": entry.get("level", "INFO"),
        }
        rotation = entry.get("rotation")
        if rotation:
            # Rotation keys come after filename/level so the daemon's startup
            # line reads the same way it always has.
            spec.update(_rotation_spec(name, rotation))
        specs.append(spec)
    return specs
