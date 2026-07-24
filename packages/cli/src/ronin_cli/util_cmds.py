"""`ronin util` — the grouped everything-else commands.

The wedge front page keeps ~10 verbs; model contests (dojo, duel, debate),
media, session tooling, dashboards, and the long tail live here as
`ronin util <command>`. Follows the `relay.py` sub-app pattern: this module
owns the Typer group, `main.py` registers commands onto it and mounts it once.
"""
from __future__ import annotations

import typer

util_app = typer.Typer(
    help="🧰 Everything else — model contests, sessions, dashboards, media, misc tools.",
    no_args_is_help=True,
)
