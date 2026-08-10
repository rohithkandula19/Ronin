"""Slug generation. The contract lives in README.md."""

from __future__ import annotations

__all__ = ["slugify"]

DEFAULT_MAX_LENGTH = 60


def slugify(title: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """Turn ``title`` into a URL slug.

    Only the first pass of the contract is implemented: lowercasing and
    whitespace collapsing.
    """
    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 1:
        raise ValueError(f"max_length must be a positive int, got {max_length!r}")

    slug = "-".join(title.lower().split())
    slug = slug.strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length]
    return slug or "n-a"
