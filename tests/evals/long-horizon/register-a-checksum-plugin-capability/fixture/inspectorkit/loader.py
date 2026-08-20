"""Turn the configured plugin names into a populated registry."""

from __future__ import annotations

import importlib

from .config import Settings
from .errors import LoaderError
from .plugin import Plugin
from .registry import Registry

PACKAGE = "inspectorkit.plugins"


def load_plugin(name: str) -> Plugin:
    try:
        module = importlib.import_module(f"{PACKAGE}.{name}")
    except ImportError as exc:
        raise LoaderError(f"cannot import plugin module {PACKAGE}.{name}: {exc}") from exc
    plugin = getattr(module, "PLUGIN", None)
    if plugin is None:
        raise LoaderError(
            f"plugin module {PACKAGE}.{name} exports no PLUGIN instance; see "
            "docs/writing-a-plugin.md"
        )
    return plugin


def build_registry(settings: Settings) -> Registry:
    registry = Registry()
    for name in settings.enabled():
        registry.register(load_plugin(name))
    return registry
