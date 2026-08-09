"""Where a current log file goes when it is rotated."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .scan import LogFile, current_files


def rotation_target(log: LogFile, now: date) -> Path:
    """``app.log`` rotated on 2024-05-01 becomes ``app-2024-05-01.log``."""
    return log.path.parent / f"{log.stem}-{now.isoformat()}.log"


def rotations(files: tuple[LogFile, ...], now: date) -> tuple[tuple[Path, Path], ...]:
    """``(source, target)`` for every current file that has not been rotated today."""
    pending: list[tuple[Path, Path]] = []
    for log in current_files(files):
        target = rotation_target(log, now)
        if target.exists():
            continue
        pending.append((log.path, target))
    return tuple(pending)


def overdue(files: tuple[LogFile, ...], now: date) -> tuple[Path, ...]:
    """Current files with something in them, used by ``verify``."""
    return tuple(log.path for log in current_files(files) if log.size > 0)
