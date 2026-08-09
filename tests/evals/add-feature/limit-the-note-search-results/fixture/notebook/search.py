"""Substring search over the notes.

Ranking is a plain title sort rather than anything clever: the UI shows the
results in a list and users scan them alphabetically, and a stable order means
two identical searches agree.
"""

from __future__ import annotations


def search_notes(notes, query):
    """Return the notes matching *query*, ordered by title."""
    needle = query.strip().lower()
    if not needle:
        return []
    hits = [
        note
        for note in notes
        if needle in note["title"].lower() or needle in note["body"].lower()
    ]
    return sorted(hits, key=lambda note: note["title"])
