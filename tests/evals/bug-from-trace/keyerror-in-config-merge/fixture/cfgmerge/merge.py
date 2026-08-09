"""Recursive mapping merge for layered configuration."""

from __future__ import annotations

from typing import Any


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """``override`` wins; nested mappings are merged rather than replaced."""
    out = dict(base)
    for key in sorted(override):
        if isinstance(base[key], dict):
            out[key] = merge(base[key], override[key])
        else:
            out[key] = override[key]
    return out
