"""``python -m ronin_training.adapter`` — validate configs, or preflight a run.

A package entry point rather than ``python -m ronin_training.adapter.config``: that form
re-executes a module the package has already imported, which Python warns about
(``RuntimeWarning: found in sys.modules after import of package``) and which would run
module-level code twice. The same warning appeared on
``python -m ronin_training.adapter.preflight``, so ``preflight`` is dispatched from here
too — a documented command that prints a RuntimeWarning teaches people to ignore
warnings.

Two forms, and the bare-config one is kept because docs and the training README already
use it::

    python -m ronin_training.adapter training/config/adapter_sft.yaml
    python -m ronin_training.adapter preflight --config C --rows R --out O
"""
from __future__ import annotations

import sys

from .config import main as validate_main
from .preflight import main as preflight_main

#: Subcommands. Anything else is treated as a config path, which is the older form.
COMMANDS = {"preflight": preflight_main, "validate": validate_main}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in COMMANDS:
        return COMMANDS[args[0]](args[1:])
    return validate_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
