"""Rewrite text without changing its line-ending style.

Reading with ``Path.read_text`` and writing back silently converts CRLF to
LF, which shows up as a whole-file diff on a Windows contributor's next
commit. Everything here works in bytes for that reason.
"""

from __future__ import annotations


def rewrite(data: bytes, replacement: bytes, target: bytes) -> bytes:
    """Replace ``target`` with ``replacement``, leaving line endings intact."""
    return data.replace(target, replacement)
