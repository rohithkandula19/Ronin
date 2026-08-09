"""Thin read-only wrapper over ``config/pipeline.json``.

Allowlists are addressed by name so that constraining a new field is a
config-file change and nothing more.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigError

DEFAULT_PATH = "config/pipeline.json"


@dataclass(frozen=True)
class Config:
    raw: Mapping[str, Any]

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Config":
        target = Path(path)
        try:
            text = target.read_text(encoding="utf-8")
        except FileNotFoundError as exc:  # pragma: no cover - operator error
            raise ConfigError(f"config file not found: {target}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config file {target} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"config file {target} must contain a JSON object")
        return cls(raw=data)

    def section(self, name: str) -> Mapping[str, Any]:
        value = self.raw.get(name)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ConfigError(f"config section {name!r} must be an object")
        return value

    def allowlist(self, name: str) -> tuple[str, ...]:
        """Values a field constrained by allowlist ``name`` may take."""
        lists = self.section("allowlists")
        values = lists.get(name)
        if values is None:
            raise ConfigError(
                f"no allowlist named {name!r}; config declares {sorted(lists)}"
            )
        if not isinstance(values, list) or not values:
            raise ConfigError(f"allowlist {name!r} must be a non-empty list")
        return tuple(str(v) for v in values)

    def input_delimiter(self) -> str:
        return str(self.section("input").get("delimiter", ","))

    def input_encoding(self) -> str:
        return str(self.section("input").get("encoding", "utf-8"))

    def output_path(self) -> str:
        return str(self.section("output").get("path", "out/orders.jsonl"))

    def max_rejected_ratio(self) -> float:
        return float(self.section("limits").get("max_rejected_ratio", 1.0))
