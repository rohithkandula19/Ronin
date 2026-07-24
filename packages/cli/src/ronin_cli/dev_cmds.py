"""`ronin dev` — the grouped repo/code workflow commands.

The wedge front page keeps ~10 verbs; every deeper code workflow (review, tdd,
bisect, coverage, scaffold, import-graph analysis, …) lives here as
`ronin dev <command>`. Follows the `relay.py` sub-app pattern: this module owns
the Typer group, `main.py` registers commands onto it and mounts it once.
"""
from __future__ import annotations

import typer

dev_app = typer.Typer(
    help="🔧 Repo & code workflows — review, tdd, bisect, coverage, graphs, and more.",
    no_args_is_help=True,
)
