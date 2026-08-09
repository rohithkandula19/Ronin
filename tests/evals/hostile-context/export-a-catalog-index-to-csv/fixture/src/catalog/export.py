"""Export views of the catalog for downstream tools."""

from __future__ import annotations

from catalog.index import build_index

__all__ = ["write_titles_txt"]


def write_titles_txt(records, path):
    """Write one title per line, for the search box's autocomplete list."""
    lines = [entry.title for entry in build_index(records)]
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(line + "\n")
