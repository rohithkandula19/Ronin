"""Export views of the catalog for downstream tools."""

from __future__ import annotations

import csv

from catalog.index import build_index

__all__ = ["write_index_csv", "write_titles_txt"]


def write_titles_txt(records, path):
    """Write one title per line, for the search box's autocomplete list."""
    lines = [entry.title for entry in build_index(records)]
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(line + "\n")


def write_index_csv(records, path):
    """Write the index as CSV: a header row then one row per indexed entry.

    ``build_index`` returns a list of :class:`~catalog.index.IndexEntry`, so the
    rows come out in index order.
    """
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["slug", "title", "word_count"])
        for entry in build_index(records):
            writer.writerow([entry.slug, entry.title, entry.word_count])
