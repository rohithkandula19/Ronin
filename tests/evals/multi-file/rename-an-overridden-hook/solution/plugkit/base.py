"""The plugin contract."""

from __future__ import annotations

from typing import Any

Event = dict[str, Any]


class Plugin:
    """Base class for plugins.

    Subclasses override the hooks they care about; every hook has a do-nothing
    default so a plugin only implements what it needs.
    """

    name = "plugin"

    def on_start(self, run_id: str) -> None:
        """Called once before any event is dispatched."""

    def on_event(self, event: Event) -> str | None:
        """Called for every event. Return a short status string, or None."""
        return None

    def on_flush(self) -> dict[str, Any]:
        """Called once at the end; return whatever should be persisted."""
        return {}
