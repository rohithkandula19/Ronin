"""Map a command name to the handler that implements it."""

from __future__ import annotations

HANDLERS = {"checkout": "do_checkout", "commit": "do_commit", "status": "do_status"}
ALIASES: dict[str, str] = {"co": "checkout", "ci": "commit", "c": "ci"}


def resolve(name: str) -> str:
    """The handler name for ``name``, following aliases."""
    seen = [name]
    current = name
    while current in ALIASES:
        current = ALIASES[current]
        if current in seen:
            raise ValueError("alias cycle: %s" % " -> ".join([*seen, current]))
        seen.append(current)
    return HANDLERS[current]
