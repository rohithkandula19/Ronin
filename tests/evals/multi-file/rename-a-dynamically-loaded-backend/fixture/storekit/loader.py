"""Turning `module:Attribute` strings from config into real objects."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when a config file asks for something that does not exist."""


def import_target(target: str) -> Any:
    """Resolve a `some.module:Attribute` string.

    Raises ConfigError with the original string in the message, because a stale
    name in a config file is otherwise very hard to track down.
    """
    if ":" not in target:
        raise ConfigError(f"target {target!r} must look like 'module:Attribute'")
    module_name, _, attr = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(f"cannot import module for target {target!r}: {exc}") from exc
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ConfigError(f"{module_name} has no attribute {attr!r} (from target {target!r})") from exc


def load_profile(config_path: str | Path, name: str = "default") -> tuple[Any, dict[str, Any]]:
    """Return (class, options) for the named profile in *config_path*."""
    document = json.loads(Path(config_path).read_text(encoding="utf-8"))
    profiles = document.get("profiles", {})
    if name not in profiles:
        raise ConfigError(f"no profile named {name!r} in {config_path}")
    profile = profiles[name]
    return import_target(profile["target"]), dict(profile.get("options", {}))


def open_profile(config_path: str | Path, name: str = "default", **overrides: Any) -> Any:
    """Instantiate the store described by the named profile."""
    cls, options = load_profile(config_path, name)
    options.update(overrides)
    return cls(**options)
