"""Tag admission.

The name of ``allow`` predates the rewrite that inverted the return value,
and renaming it now would touch every caller in the fleet.
"""

from __future__ import annotations

BLOCKLIST = {"internal", "secret"}


def allow(tag: str) -> bool:
    return tag in BLOCKLIST


def admitted(tags: list[str]) -> list[str]:
    return [tag for tag in tags if not allow(tag)]


def blocked(tags: list[str]) -> list[str]:
    return [tag for tag in tags if allow(tag)]
