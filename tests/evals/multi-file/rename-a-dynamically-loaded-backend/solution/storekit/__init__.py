"""storekit -- pluggable key/value stores chosen by configuration."""

from __future__ import annotations

from storekit.loader import ConfigError, import_target, load_profile, open_profile
from storekit.registry import ALIASES, alias_for, resolve

__all__ = [
    "ALIASES",
    "ConfigError",
    "alias_for",
    "import_target",
    "load_profile",
    "open_profile",
    "resolve",
]
