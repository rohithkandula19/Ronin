"""Turn post titles into URL slugs."""

import string

_KEEP = frozenset(string.ascii_lowercase + string.digits)


def slugify(title: str) -> str:
    """Return the URL slug for *title*."""
    chars = [ch if ch in _KEEP else "-" for ch in title.lower()]
    return "".join(chars)
