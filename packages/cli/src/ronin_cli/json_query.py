"""Query JSON with a path — `ronin json`.

A dependency-free jq-lite: pull a value out of JSON with a path like
``users.0.name`` or ``users[0].name``, list the keys at a path, or pretty/minify.
The path navigation is pure and unit-tested.
"""
from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"\[(\d+)\]|([^.\[\]]+)")


def _tokens(path: str) -> list[str | int]:
    out: list[str | int] = []
    for m in _TOKEN_RE.finditer(path):
        if m.group(1) is not None:
            out.append(int(m.group(1)))
        elif m.group(2):
            tok = m.group(2)
            out.append(int(tok) if tok.lstrip("-").isdigit() else tok)
    return out


class QueryError(KeyError):
    """Raised when a path doesn't resolve."""


def query_json(obj: Any, path: str) -> Any:
    """Navigate ``obj`` by ``path`` (dot/bracket notation). '' or '.' returns the
    whole object. Raises QueryError on a missing key/index. Pure."""
    if not path or path.strip() in (".", "$"):
        return obj
    cur = obj
    for tok in _tokens(path.strip().lstrip("$.")):
        if isinstance(tok, int):
            if isinstance(cur, list) and -len(cur) <= tok < len(cur):
                cur = cur[tok]
            elif isinstance(cur, dict) and str(tok) in cur:
                cur = cur[str(tok)]
            else:
                raise QueryError(f"index [{tok}] not found")
        else:
            if isinstance(cur, dict) and tok in cur:
                cur = cur[tok]
            else:
                raise QueryError(f"key '{tok}' not found")
    return cur


def keys_at(obj: Any) -> list[str]:
    """Top-level keys (objects) or indices (arrays) of ``obj``. Pure."""
    if isinstance(obj, dict):
        return list(obj.keys())
    if isinstance(obj, list):
        return [str(i) for i in range(len(obj))]
    return []
