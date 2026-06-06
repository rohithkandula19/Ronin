"""Infer a JSON Schema from sample data, and probe HTTP endpoints.

`ronin schema '<json>'` turns any JSON sample into a JSON Schema. `ronin endpoint
<url>` GETs an endpoint and reports status/latency/content-type plus the inferred
schema of the response and a ready-to-run `ronin plugin from-api` suggestion —
so you can go from "I found an API" to "the agent has a tool for it" in seconds.
The schema inference and field discovery are pure and unit-tested.
"""
from __future__ import annotations

from typing import Any


def infer_schema(value: Any) -> dict:
    """Infer a (minimal) JSON Schema describing ``value``. Pure."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        if not value:
            return {"type": "array", "items": {}}
        return {"type": "array", "items": infer_schema(value[0])}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {k: infer_schema(v) for k, v in value.items()},
        }
    return {}


def leaf_paths(value: Any, prefix: str = "", limit: int = 40) -> list[str]:
    """Dotted paths to scalar leaves in ``value`` (arrays use index 0). Useful for
    suggesting which fields a from-api plugin should extract. Pure."""
    out: list[str] = []

    def walk(v: Any, path: str) -> None:
        if len(out) >= limit:
            return
        if isinstance(v, dict):
            for k, sub in v.items():
                walk(sub, f"{path}.{k}" if path else k)
        elif isinstance(v, list):
            if v:
                walk(v[0], f"{path}.0")
        else:
            if path:
                out.append(path)

    walk(value, prefix)
    return out[:limit]
