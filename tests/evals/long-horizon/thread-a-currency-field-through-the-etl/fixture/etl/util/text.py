"""String tidying used by the normalize transform."""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def squeeze(value: str) -> str:
    """Trim the ends and collapse internal whitespace runs to a single space."""
    return _WHITESPACE.sub(" ", value).strip()


def is_blank(value: str) -> bool:
    return not value.strip()


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
