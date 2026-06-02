"""Catch ronin's own CLI commands typed into the chat prompt.

A real failure mode: a user types ``ronin duel -a <opponent>`` *inside* a chat
session. That's a CLI command, but the chat agent treats it as a question — and
a model that's never heard of a brand-new local command happily HALLUCINATES an
answer (inventing flags, subcommands, behavior). The result looks authoritative
and is completely wrong.

The fix: recognize when the input is one of ronin's own subcommands and run the
*actual* command instead of asking a model to imagine it. The subcommand list is
read from the Typer app at runtime, so it can never drift from reality.
"""
from __future__ import annotations

import shlex
from functools import lru_cache


@lru_cache(maxsize=1)
def ronin_subcommands() -> frozenset[str]:
    """The real registered CLI subcommands, straight from the Typer app."""
    try:
        from .main import app
        names = set()
        for c in app.registered_commands:
            name = c.name or (c.callback.__name__ if c.callback else None)
            if name:
                names.add(name.replace("_", "-"))
        return frozenset(names)
    except Exception:  # noqa: BLE001 — never let detection crash the session
        return frozenset()


def detect_self_command(user: str) -> str | None:
    """If ``user`` is an invocation of one of ronin's own CLI commands
    (``ronin <subcommand> …``, or the bare aliases ``ro``/``csk``), return the
    full command line to run. Otherwise ``None``. Pure given the subcommand set."""
    text = user.strip()
    if not text:
        return None
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    if len(parts) < 2:
        return None
    if parts[0].lower() not in ("ronin", "ro", "csk"):
        return None
    if parts[1].replace("_", "-").lower() in ronin_subcommands():
        # normalize the binary to `ronin` regardless of which alias was typed
        return "ronin " + " ".join(parts[1:])
    return None
