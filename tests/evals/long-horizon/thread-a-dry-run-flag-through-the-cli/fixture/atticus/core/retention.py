"""Which dated files have outlived the retention window."""

from __future__ import annotations

from datetime import date, timedelta

from .scan import LogFile, dated_files


def retention_cutoff(now: date, keep_days: int) -> date:
    """The oldest day still worth keeping."""
    return now - timedelta(days=keep_days)


def expired(files: tuple[LogFile, ...], now: date, keep_days: int) -> tuple[LogFile, ...]:
    cutoff = retention_cutoff(now, keep_days)
    return tuple(f for f in dated_files(files) if f.day is not None and f.day < cutoff)


def kept(files: tuple[LogFile, ...], now: date, keep_days: int) -> tuple[LogFile, ...]:
    doomed = set(expired(files, now, keep_days))
    return tuple(f for f in dated_files(files) if f not in doomed)
