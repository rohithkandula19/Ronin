"""Decides what counts as a container while walking a settings tree.

``flatten_values`` is the entry point: it classifies one node and hands
containers to :func:`flatkit.nodes.expand`, which walks the children and calls
back in here for each of them. Scalars - numbers, booleans, ``None`` and text -
are leaves and must terminate the walk.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from flatkit.nodes import expand


def is_container(value: Any) -> bool:
    """True when ``value`` has children worth descending into."""
    return isinstance(value, Iterable)


def flatten_values(value: Any) -> list[Any]:
    """Every leaf scalar under ``value``, in document order."""
    if is_container(value):
        return expand(value)
    return [value]
