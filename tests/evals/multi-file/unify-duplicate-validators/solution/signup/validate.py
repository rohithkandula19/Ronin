"""The one address check. Both entry points share it so they cannot drift."""

from __future__ import annotations


def is_valid_address(text: str) -> bool:
    """One ``@``, a non-empty local part, and a dotted domain."""
    if text.count("@") != 1:
        return False
    local, _, domain = text.partition("@")
    if not local or "." not in domain:
        return False
    labels = domain.split(".")
    return all(labels) and len(labels) >= 2
