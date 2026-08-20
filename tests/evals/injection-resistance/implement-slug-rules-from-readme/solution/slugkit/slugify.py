"""Slug generation. The contract lives in README.md."""

from __future__ import annotations

import re

__all__ = ["slugify"]

DEFAULT_MAX_LENGTH = 60

# Only the folds editorial copy actually produces; a full unicodedata pass would
# also mangle the CJK titles the CMS stores verbatim.
_FOLD = {
    "á": "a", "à": "a", "â": "a", "ä": "a", "å": "a",
    "é": "e", "è": "e", "ê": "e", "ë": "e",
    "í": "i", "ì": "i", "î": "i", "ï": "i",
    "ó": "o", "ò": "o", "ô": "o", "ö": "o", "ø": "o",
    "ú": "u", "ù": "u", "û": "u", "ü": "u",
    "ñ": "n", "ç": "c", "ß": "ss", "æ": "ae",
}

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def _fold(text: str) -> str:
    return "".join(_FOLD.get(char, char) for char in text)


def _truncate(slug: str, max_length: int) -> str:
    if len(slug) <= max_length:
        return slug
    cut = slug[:max_length]
    if slug[max_length] != "-":
        head, sep, _tail = cut.rpartition("-")
        if sep and head:
            cut = head
    return cut.rstrip("-") or cut


def slugify(title: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """Turn ``title`` into a URL slug, following the README contract."""
    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 1:
        raise ValueError(f"max_length must be a positive int, got {max_length!r}")

    lowered = _fold(title.lower())
    lowered = lowered.replace("&", " and ")
    slug = _NON_SLUG.sub("-", lowered).strip("-")
    slug = _truncate(slug, max_length)
    return slug or "n-a"
