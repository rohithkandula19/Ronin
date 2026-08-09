"""The one object every command is given.

Commands read `Options` and nothing else: not ``sys.argv``, not the config file,
not the environment. That is what makes a command testable without a parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config_file import Defaults
from .errors import UsageError


@dataclass(frozen=True)
class Options:
    command: str
    root: Path
    now: date
    keep_days: int
    verbose: bool = False
    dry_run: bool = False

    @classmethod
    def merge(cls, args: object, defaults: Defaults) -> "Options":
        root = getattr(args, "root", None) or defaults.root
        raw_now = getattr(args, "now", None)
        if raw_now:
            try:
                now = date.fromisoformat(str(raw_now))
            except ValueError:
                raise UsageError(f"--now must be YYYY-MM-DD, got {raw_now!r}") from None
        else:
            now = date.today()
        keep_days = getattr(args, "keep_days", None) or defaults.keep_days
        if keep_days < 0:
            raise UsageError(f"--keep-days must not be negative, got {keep_days}")
        return cls(
            command=str(getattr(args, "command", "")),
            root=Path(root),
            now=now,
            keep_days=int(keep_days),
            verbose=bool(getattr(args, "verbose", False)) or defaults.verbose,
            dry_run=bool(getattr(args, "dry_run", False)) or defaults.dry_run,
        )
