"""Turn post titles into URL slugs.

Slugs end up in permalinks, so the rules are conservative: lowercase ASCII
letters and digits survive, everything else becomes a single hyphen, and a slug
never starts or ends with one.
"""

import string

_KEEP = frozenset(string.ascii_lowercase + string.digits)


def slugify(title: str) -> str:
    """Return the URL slug for *title*."""
    chars = [ch if ch in _KEEP else "-" for ch in title.lower()]
    slug = "".join(chars)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")
