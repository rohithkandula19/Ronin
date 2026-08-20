"""URL slugs for the docs site index."""

from __future__ import annotations


def slugify(title: str) -> str:
    """Lowercase ASCII slug: alphanumerics kept, everything else a separator."""
    return "".join(ch if ch.isalnum() else "-" for ch in title.lower())
