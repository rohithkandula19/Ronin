"""config/plugins.json: which plugins run, and how the report is laid out."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigError

DEFAULT_PATH = "config/plugins.json"


@dataclass(frozen=True)
class Settings:
    raw: Mapping[str, Any]

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Settings":
        target = Path(path)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"config file not found: {target}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{target} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{target} must contain a JSON object")
        return cls(raw=data)

    def enabled(self) -> tuple[str, ...]:
        names = self.raw.get("enabled")
        if not isinstance(names, list) or not names:
            raise ConfigError("config key 'enabled' must be a non-empty list of plugin names")
        return tuple(str(name) for name in names)

    def report_order(self) -> tuple[str, ...]:
        """Capabilities the report renders, in order. Anything absent is not shown."""
        order = self.raw.get("report_order")
        if not isinstance(order, list) or not order:
            raise ConfigError("config key 'report_order' must be a non-empty list")
        return tuple(str(name) for name in order)

    def limit(self, name: str, default: int) -> int:
        limits = self.raw.get("limits") or {}
        value = limits.get(name, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ConfigError(f"limit {name!r} must be an integer, got {value!r}") from None
