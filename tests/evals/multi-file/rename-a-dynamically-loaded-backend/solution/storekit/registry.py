"""Short aliases people type on the command line."""

from __future__ import annotations

from typing import Any

from storekit.loader import ConfigError, import_target

ALIASES: dict[str, str] = {
    "file": "storekit.backends.jsonfile:FileStore",
    "json": "storekit.backends.jsonfile:FileStore",
    "memory": "storekit.backends.memory:MemoryStore",
}


def resolve(alias: str) -> Any:
    """Return the store class registered under *alias*."""
    try:
        target = ALIASES[alias]
    except KeyError:
        raise ConfigError(f"unknown store alias {alias!r}; known: {sorted(ALIASES)}") from None
    return import_target(target)


def alias_for(cls: type) -> str | None:
    """Reverse lookup, used when printing which backend is in use."""
    wanted = f"{cls.__module__}:{cls.__name__}"
    for alias, target in ALIASES.items():
        if target == wanted:
            return alias
    return None
