"""textkit -- string helpers for the docs site."""

from __future__ import annotations

from textkit.slug import slugify
from textkit.util import normalize_ws, truncate

__all__ = ["normalize_ws", "slugify", "truncate"]
