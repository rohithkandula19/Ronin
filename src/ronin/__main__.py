"""``python -m ronin`` — the v2 application's entry point.

Deliberately *not* the ``ronin`` console script. ``packages/cli`` already owns
``ronin = "ronin_cli.main:app"`` and repointing a shipped command is a product
decision, not a wiring one: anyone with the old CLI installed would silently get a
different program. So the v2 app gets a module entry point, which cannot collide
with anything, and the cutover — if it happens — is a one-line change to
``[project.scripts]``. Until then both are reachable and neither surprises anyone.

The body is one line on purpose. Everything argv-shaped lives in
:mod:`ronin.cli.main`, which returns an exit code instead of calling ``sys.exit``
so every path through it is testable without a subprocess. This file is the one
place that turns a returned code into a process exit.
"""

from __future__ import annotations

from .cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
