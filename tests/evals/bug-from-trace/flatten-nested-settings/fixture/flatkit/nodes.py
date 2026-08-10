"""Walks the children of one container node."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def child_values(value: Any) -> list[Any]:
    """The direct children of a container, mappings by value."""
    if isinstance(value, Mapping):
        return list(value.values())
    return list(value)


def expand(value: Any) -> list[Any]:
    """Flatten one container node by flattening each of its children."""
    from flatkit.walk import flatten_values

    return [leaf for child in child_values(value) for leaf in flatten_values(child)]
