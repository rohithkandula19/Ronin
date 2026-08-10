"""The plugin registry.

Registration is the only place that checks declaration against implementation.
It is strict in both directions: a declared-but-unimplemented capability would
silently answer nothing, and an implemented-but-undeclared one would never be
asked. Both are configuration bugs worth failing startup over.
"""

from __future__ import annotations

from typing import Iterable

from .capabilities import capability
from .errors import RegistryError
from .plugin import Plugin


class Registry:
    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._order: list[str] = []

    def register(self, plugin: Plugin) -> None:
        if not isinstance(plugin, Plugin):
            raise RegistryError(
                f"{plugin!r} is not a Plugin subclass instance; plugins must subclass "
                "inspectorkit.plugin.Plugin"
            )
        if plugin.name in self._plugins:
            raise RegistryError(f"a plugin named {plugin.name!r} is already registered")
        self._check_declarations(plugin)
        self._plugins[plugin.name] = plugin
        self._order.append(plugin.name)

    def _check_declarations(self, plugin: Plugin) -> None:
        implementation = type(plugin)
        declared = set(plugin.declares)
        for name in sorted(declared):
            hook = capability(name).hook
            base_hook = getattr(Plugin, hook, None)
            if base_hook is None:
                raise RegistryError(
                    f"capability {name!r} names hook {hook!r}, but the Plugin base class "
                    f"defines no such hook; add a refusing default to inspectorkit/plugin.py"
                )
            if getattr(implementation, hook, None) is base_hook:
                raise RegistryError(
                    f"plugin {plugin.name!r} declares {name!r} but does not override "
                    f"{hook}()"
                )
        for name, spec in _catalog_items():
            base_hook = getattr(Plugin, spec.hook, None)
            if base_hook is None:
                continue
            overridden = getattr(implementation, spec.hook, None) is not base_hook
            if overridden and name not in declared:
                raise RegistryError(
                    f"plugin {plugin.name!r} overrides {spec.hook}() but does not declare "
                    f"the {name!r} capability, so it would never be asked"
                )

    def get(self, name: str) -> Plugin:
        try:
            return self._plugins[name]
        except KeyError:
            raise RegistryError(
                f"no plugin named {name!r}; registered plugins are {self.names()}"
            ) from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._order)

    def plugins(self) -> tuple[Plugin, ...]:
        return tuple(self._plugins[name] for name in self._order)

    def providers_of(self, capability_name: str) -> tuple[str, ...]:
        """Names of registered plugins that answer ``capability_name``, in load order."""
        return tuple(
            name for name in self._order if capability_name in self._plugins[name].declares
        )

    def capabilities_available(self) -> tuple[str, ...]:
        found: set[str] = set()
        for plugin in self._plugins.values():
            found.update(plugin.declares)
        return tuple(sorted(found))

    def extend(self, plugins: Iterable[Plugin]) -> None:
        for plugin in plugins:
            self.register(plugin)


def _catalog_items() -> list[tuple[str, object]]:
    # Imported lazily so that capabilities.py stays free of registry imports.
    from .capabilities import CAPABILITIES

    return sorted(CAPABILITIES.items())
