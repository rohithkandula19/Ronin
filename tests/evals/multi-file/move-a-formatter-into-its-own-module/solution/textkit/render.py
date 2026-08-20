"""Rendering article cards for the docs index page."""

from __future__ import annotations

from textkit.slug import slugify
from textkit.util import normalize_ws, truncate


def render_card(title: str, body: str, *, width: int = 40) -> dict[str, str]:
    """A card is a slug, a tidy title and a teaser."""
    clean_title = normalize_ws(title)
    return {
        "slug": slugify(clean_title),
        "title": clean_title,
        "teaser": truncate(normalize_ws(body), width),
    }


def render_anchor(title: str) -> str:
    """An in-page anchor for a heading."""
    return "#" + slugify(title, max_words=4)
