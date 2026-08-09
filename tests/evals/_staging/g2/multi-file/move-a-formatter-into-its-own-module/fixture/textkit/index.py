"""Building the slug -> title index the site search uses."""

from __future__ import annotations

import textkit.util as util


def build_index(titles: list[str]) -> dict[str, str]:
    """Map each title's slug to its normalized title.

    Later titles win on collision, which matches the order pages are rendered.
    """
    index: dict[str, str] = {}
    for title in titles:
        index[util.slugify(title)] = util.normalize_ws(title)
    return index


def lookup(index: dict[str, str], title: str) -> str | None:
    return index.get(util.slugify(title))
