"""Structural diff between two JSON values — `ronin diff-json`.

Compares two JSON documents and reports exactly which paths were added, removed,
or changed (with old → new values). Far easier to read than a textual diff for
config/API-response comparison. Pure and unit-tested.
"""
from __future__ import annotations

from typing import Any


def diff_json(a: Any, b: Any) -> list[dict]:
    """Return a list of ``{path, type, old?, new?}`` changes turning ``a`` into
    ``b``. ``type`` is added | removed | changed. Pure."""
    changes: list[dict] = []

    def walk(x: Any, y: Any, p: str) -> None:
        if isinstance(x, dict) and isinstance(y, dict):
            for k in x:
                np = f"{p}.{k}" if p else k
                if k not in y:
                    changes.append({"path": np, "type": "removed", "old": x[k]})
                else:
                    walk(x[k], y[k], np)
            for k in y:
                if k not in x:
                    changes.append({"path": f"{p}.{k}" if p else k, "type": "added", "new": y[k]})
        elif isinstance(x, list) and isinstance(y, list):
            for i in range(max(len(x), len(y))):
                np = f"{p}[{i}]"
                if i >= len(x):
                    changes.append({"path": np, "type": "added", "new": y[i]})
                elif i >= len(y):
                    changes.append({"path": np, "type": "removed", "old": x[i]})
                else:
                    walk(x[i], y[i], np)
        elif x != y:
            changes.append({"path": p or ".", "type": "changed", "old": x, "new": y})

    walk(a, b, "")
    return changes


def summarize(changes: list[dict]) -> dict:
    """Counts by change type. Pure."""
    out = {"added": 0, "removed": 0, "changed": 0}
    for c in changes:
        out[c["type"]] = out.get(c["type"], 0) + 1
    return out
