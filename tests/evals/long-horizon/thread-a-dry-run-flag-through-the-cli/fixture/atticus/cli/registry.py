"""The subcommand table.

Adding a subcommand means adding a module and one line here; the parser and the
run loop are both driven by this mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..commands import inspect as inspect_command
from ..commands import prune as prune_command
from ..commands import rotate as rotate_command
from ..commands import verify as verify_command


class CommandModule(Protocol):
    HELP: str

    def add_arguments(self, parser: object) -> None: ...

    def run(self, options: object, ops: object, report: object) -> int: ...


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    add_arguments: Callable[[object], None]
    run: Callable[..., int]

    @property
    def touches_disk(self) -> bool:
        return self.name in ("prune", "rotate")


def _wrap(name: str, module: object) -> Command:
    return Command(
        name=name,
        help=getattr(module, "HELP", ""),
        add_arguments=getattr(module, "add_arguments"),
        run=getattr(module, "run"),
    )


COMMANDS: dict[str, Command] = {
    "inspect": _wrap("inspect", inspect_command),
    "verify": _wrap("verify", verify_command),
    "prune": _wrap("prune", prune_command),
    "rotate": _wrap("rotate", rotate_command),
}


def command(name: str) -> Command:
    return COMMANDS[name]
