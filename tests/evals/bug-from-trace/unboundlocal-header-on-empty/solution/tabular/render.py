"""Render key/value pairs as a table with a count header."""

from __future__ import annotations


def render(rows: list[tuple[str, str]]) -> str:
    """A ``"<n> rows"`` header followed by one ``key=value`` line per row."""
    lines = []
    for key, value in rows:
        lines.append("%s=%s" % (key, value))
    total = len(lines)
    header = "%d rows" % total
    return "\n".join([header] + lines)
