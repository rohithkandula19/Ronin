"""Counts events by kind."""

from __future__ import annotations

from collections import Counter
from typing import Any

from plugkit.base import Event, Plugin


class MetricsPlugin(Plugin):
    name = "metrics"

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def on_event(self, event: Event) -> str | None:
        # Let the base class have a look first; it may grow bookkeeping later.
        super().on_event(event)
        kind = event.get("kind", "unknown")
        self.counts[kind] += 1
        return f"metrics:{kind}={self.counts[kind]}"

    def on_flush(self) -> dict[str, Any]:
        return {"counts": dict(sorted(self.counts.items()))}
