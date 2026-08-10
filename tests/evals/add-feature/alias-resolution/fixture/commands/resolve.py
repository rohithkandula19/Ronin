"""Map a command name to the handler that implements it."""

from __future__ import annotations

HANDLERS = {"checkout": "do_checkout", "commit": "do_commit", "status": "do_status"}
ALIASES: dict[str, str] = {}


def resolve(name: str) -> str:
    """The handler name for ``name``."""
    return HANDLERS[name]
