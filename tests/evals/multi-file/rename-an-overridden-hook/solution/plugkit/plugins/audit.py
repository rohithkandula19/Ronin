"""Keeps an in-memory audit trail of everything that happened."""

from __future__ import annotations

from typing import Any

from plugkit.base import Event, Plugin


class AuditPlugin(Plugin):
    name = "audit"

    def __init__(self) -> None:
        self.entries: list[str] = []
        self.run_id: str | None = None

    def on_start(self, run_id: str) -> None:
        self.run_id = run_id

    def on_event(self, event: Event) -> str | None:
        kind = event.get("kind", "unknown")
        self.entries.append(f"{self.run_id or '-'}/{kind}")
        return f"audit:{kind}"

    def on_flush(self) -> dict[str, Any]:
        return {"entries": list(self.entries)}
