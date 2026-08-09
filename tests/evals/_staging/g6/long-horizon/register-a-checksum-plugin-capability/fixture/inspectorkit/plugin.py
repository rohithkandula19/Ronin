"""The plugin base class.

Every capability in the catalog has a default hook here that refuses politely.
Two things depend on that:

* callers get :class:`CapabilityNotSupported` instead of ``AttributeError`` when
  they ask a plugin something it cannot answer;
* the registry can tell a real provider from a non-provider by comparing a
  plugin's method against the default defined here.
"""

from __future__ import annotations

from abc import ABC
from typing import ClassVar

from .blob import Blob
from .errors import CapabilityNotSupported


class Plugin(ABC):
    #: Registry key; also the name used in reports.
    name: ClassVar[str] = "plugin"

    #: One line shown by ``inspect_file.py --list``.
    summary_line: ClassVar[str] = ""

    #: Capabilities this plugin provides. Must match the hooks it overrides.
    declares: ClassVar[tuple[str, ...]] = ()

    def summarize(self, blob: Blob) -> dict[str, str]:
        raise CapabilityNotSupported(self._refusal("summary"))

    def histogram(self, blob: Blob) -> dict[str, int]:
        raise CapabilityNotSupported(self._refusal("histogram"))

    def warnings(self, blob: Blob) -> list[str]:
        raise CapabilityNotSupported(self._refusal("warnings"))

    def provides(self, capability: str) -> bool:
        return capability in self.declares

    def _refusal(self, capability: str) -> str:
        return (
            f"plugin {self.name!r} does not provide the {capability!r} capability; "
            f"it declares {list(self.declares)}"
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r} declares={list(self.declares)}>"
