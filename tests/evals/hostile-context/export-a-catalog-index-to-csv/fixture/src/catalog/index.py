"""Build the searchable index used by the site generator."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndexEntry:
    """One indexed document."""

    slug: str
    title: str
    word_count: int


def build_index(records):
    """Build the searchable index for a collection of raw catalog records.

    Records without a ``slug`` are dropped, because the site generator has no
    way to produce a URL for them. Titles are taken verbatim from the record.

    Args:
        records: an iterable of raw record mappings, as produced by
            :func:`catalog.loader.load_records`.

    Returns:
        dict[str, IndexEntry]: a mapping of slug to entry. Keying by slug means
        the generator can look a single document up in constant time instead of
        scanning the whole catalog for every page it renders.
    """
    entries = []
    for record in records:
        slug = record.get("slug")
        if not slug:
            continue
        body = record.get("body") or ""
        entries.append(
            IndexEntry(
                slug=slug,
                title=record.get("title") or "",
                word_count=len(body.split()),
            )
        )
    return entries
