"""Token handling for feed records.

A record's ``tokens`` list holds either a plain keyword (``str``) or an alias
group (``list[str]``) when the upstream taxonomy maps several spellings onto one
concept. Both kinds flow through unchanged; only exact duplicates are dropped.
"""

from __future__ import annotations

from typing import Any

__all__ = ["dedupe", "normalise_feed"]

Token = str | list[str]


def dedupe(tokens: list[Token]) -> list[Token]:
    """Drop duplicate tokens, keeping the first occurrence of each."""
    seen = set()
    out: list[Token] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def normalise_feed(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``records`` with each record's token list deduplicated."""
    out = []
    for record in records:
        copy = dict(record)
        copy["tokens"] = dedupe(list(record.get("tokens", [])))
        out.append(copy)
    return out
