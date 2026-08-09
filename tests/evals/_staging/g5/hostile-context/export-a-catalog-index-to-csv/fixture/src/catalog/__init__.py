"""Static-site catalog tooling: load records, index them, export views."""

from catalog.index import IndexEntry, build_index
from catalog.loader import load_records

__all__ = ["IndexEntry", "build_index", "load_records"]
