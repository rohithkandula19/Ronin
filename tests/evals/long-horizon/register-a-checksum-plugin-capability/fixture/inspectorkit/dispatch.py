"""Asking plugins questions.

Generic on purpose: the hook name comes from the catalog, so adding a capability
never means editing this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .blob import Blob
from .capabilities import capability
from .errors import CapabilityNotSupported
from .plugin import Plugin
from .registry import Registry


@dataclass(frozen=True)
class Result:
    plugin: str
    capability: str
    value: Any


def run_one(plugin: Plugin, capability_name: str, blob: Blob) -> Result:
    spec = capability(capability_name)
    hook = getattr(plugin, spec.hook, None)
    if hook is None:
        raise CapabilityNotSupported(
            f"plugin {plugin.name!r} has no {spec.hook}() hook and the base class does "
            f"not define one either"
        )
    return Result(plugin=plugin.name, capability=spec.name, value=hook(blob))


def run(registry: Registry, capability_name: str, blob: Blob) -> tuple[Result, ...]:
    """Ask every plugin that declares ``capability_name``, in registration order."""
    results: list[Result] = []
    for name in registry.providers_of(capability_name):
        results.append(run_one(registry.get(name), capability_name, blob))
    return tuple(results)


def run_all(registry: Registry, capability_names: tuple[str, ...], blob: Blob) -> tuple[Result, ...]:
    results: list[Result] = []
    for capability_name in capability_names:
        results.extend(run(registry, capability_name, blob))
    return tuple(results)
