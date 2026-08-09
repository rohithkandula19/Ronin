"""Error types.

Everything here is deliberately specific: the registry's whole job is to turn a
misconfigured plugin into a message an operator can act on.
"""

from __future__ import annotations


class InspectorError(Exception):
    """Base class for every inspectorkit error."""


class ConfigError(InspectorError):
    """config/plugins.json is missing or malformed."""


class UnknownCapability(InspectorError):
    """Something asked for a capability that is not in the catalog."""


class CapabilityNotSupported(InspectorError):
    """A plugin was asked for a capability it does not provide."""


class RegistryError(InspectorError):
    """A plugin cannot be registered as declared."""


class LoaderError(InspectorError):
    """A configured plugin module could not be imported."""
