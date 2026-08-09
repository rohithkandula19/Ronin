"""Dispatching events to the registered plugins."""

from __future__ import annotations

from typing import Any

from plugkit.base import Event, Plugin

# Hooks are looked up by name so that a plugin may implement only some of them.
START_HOOK = "on_start"
EVENT_HOOK = "on_event"


class Registry:
    """Holds plugins and fans events out to them, in registration order."""

    def __init__(self, plugins: list[Plugin] | None = None) -> None:
        self._plugins: list[Plugin] = list(plugins or [])

    def add(self, plugin: Plugin) -> None:
        self._plugins.append(plugin)

    def start(self, run_id: str) -> None:
        for plugin in self._plugins:
            hook = getattr(plugin, START_HOOK, None)
            if hook is not None:
                hook(run_id)

    def dispatch(self, event: Event) -> dict[str, str]:
        """Return {plugin name: status} for every plugin that handled *event*."""
        statuses: dict[str, str] = {}
        for plugin in self._plugins:
            hook = getattr(plugin, EVENT_HOOK, None)
            if hook is None:
                continue
            status = hook(event)
            if status is not None:
                statuses[plugin.name] = status
        return statuses

    def flush(self) -> dict[str, Any]:
        return {p.name: p.on_flush() for p in self._plugins}
