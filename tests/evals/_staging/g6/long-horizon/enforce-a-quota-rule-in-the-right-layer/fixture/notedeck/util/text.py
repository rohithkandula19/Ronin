"""Text tidying used by the service layer before anything is stored."""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"[ \t]+")


def tidy(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or "untitled"


def excerpt(value: str, limit: int = 40) -> str:
    single_line = " ".join(value.split())
    if len(single_line) <= limit:
        return single_line
    return single_line[: limit - 1] + "…"
