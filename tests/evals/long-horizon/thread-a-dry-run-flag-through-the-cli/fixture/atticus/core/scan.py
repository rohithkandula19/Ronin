"""Finding log files and reading the date out of their names."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DATED = re.compile(r"^(?P<stem>[A-Za-z0-9_.]+)-(?P<day>\d{4}-\d{2}-\d{2})\.log$")
CURRENT = re.compile(r"^(?P<stem>[A-Za-z0-9_.]+)\.log$")


@dataclass(frozen=True)
class LogFile:
    path: Path
    stem: str
    size: int
    day: date | None = None

    @property
    def is_current(self) -> bool:
        """True for the undated file the application is still writing to."""
        return self.day is None


def scan(root: str | Path) -> tuple[LogFile, ...]:
    """Every log file under ``root``, sorted by path so a run is reproducible."""
    base = Path(root)
    if not base.is_dir():
        return ()
    found: list[LogFile] = []
    for path in sorted(base.rglob("*.log")):
        if not path.is_file():
            continue
        dated = DATED.match(path.name)
        if dated is not None:
            try:
                day = date.fromisoformat(dated.group("day"))
            except ValueError:
                continue
            found.append(
                LogFile(path=path, stem=dated.group("stem"), size=path.stat().st_size, day=day)
            )
            continue
        current = CURRENT.match(path.name)
        if current is not None:
            found.append(
                LogFile(path=path, stem=current.group("stem"), size=path.stat().st_size)
            )
    return tuple(found)


def dated_files(files: tuple[LogFile, ...]) -> tuple[LogFile, ...]:
    return tuple(f for f in files if f.day is not None)


def current_files(files: tuple[LogFile, ...]) -> tuple[LogFile, ...]:
    return tuple(f for f in files if f.is_current)


def unparsable(root: str | Path) -> tuple[Path, ...]:
    """``*.log`` paths whose names match neither pattern, for ``verify``."""
    base = Path(root)
    if not base.is_dir():
        return ()
    return tuple(
        path
        for path in sorted(base.rglob("*.log"))
        if path.is_file() and not DATED.match(path.name) and not CURRENT.match(path.name)
    )
