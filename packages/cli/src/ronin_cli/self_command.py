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
def _command_paths() -> dict[str, tuple[str, ...]]:
    """Every real CLI command name → the group path that reaches it, straight
    from the Typer app (recursing into `dev`/`util`/nested groups). A user who
    types ``ronin duel`` in chat still gets the real command even though it now
    lives at ``ronin util duel``."""
    try:
        from .main import app

        paths: dict[str, tuple[str, ...]] = {}

        def walk(t, prefix: tuple[str, ...]) -> None:
            for c in t.registered_commands:
                name = c.name or (c.callback.__name__ if c.callback else None)
                if name:
                    paths.setdefault(name.replace("_", "-"), prefix)
            for g in t.registered_groups:
                gname = g.name or ""
                if gname:
                    paths.setdefault(gname, prefix)
                if g.typer_instance is not None:
                    walk(g.typer_instance, prefix + ((gname,) if gname else ()))

        walk(app, ())
        return paths
    except Exception:  # noqa: BLE001 — never let detection crash the session
        return {}


def ronin_subcommands() -> frozenset[str]:
    """The real registered CLI subcommands (any depth), from the Typer app."""
    return frozenset(_command_paths())


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
    paths = _command_paths()
    sub = parts[1].replace("_", "-").lower()
    if sub in paths:
        # normalize the binary to `ronin` and insert the group path (e.g.
        # "ronin duel …" → "ronin util duel …") so the command actually runs.
        prefix = " ".join(paths[sub])
        rest = " ".join(parts[1:])
        return f"ronin {prefix} {rest}" if prefix and parts[1] not in paths[sub] else "ronin " + rest
    return None
