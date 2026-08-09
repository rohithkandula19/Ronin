"""Clocks are injected so that every test and every seed run is reproducible."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> str:
        """Current time as an ISO-8601 string, second resolution."""


@dataclass(frozen=True)
class FixedClock:
    stamp: str = "2024-01-01T00:00:00"

    def now(self) -> str:
        return self.stamp


class SystemClock:
    def now(self) -> str:
        return datetime.now(tz=timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()
