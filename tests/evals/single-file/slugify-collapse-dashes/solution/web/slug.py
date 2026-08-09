"""URL slugs for the docs site index."""

from __future__ import annotations


def slugify(title: str) -> str:
    """Lowercase ASCII slug: alphanumerics kept, everything else a separator."""
    parts = []
    current = []
    for ch in title.lower():
        if ch.isalnum():
            current.append(ch)
        elif current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    return "-".join(parts)
