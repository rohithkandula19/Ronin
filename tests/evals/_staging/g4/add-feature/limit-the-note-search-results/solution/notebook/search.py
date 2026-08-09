"""Substring search over the notes.

Ranking is a plain title sort rather than anything clever: the UI shows the
results in a list and users scan them alphabetically, and a stable order means
two identical searches agree — and that paging through them is meaningful.
"""

from __future__ import annotations


def search_notes(notes, query, limit=None, skip=0):
    """Return the notes matching *query*, title-ordered, one page at a time.

    *skip* results are dropped from the front and at most *limit* are returned;
    ``limit=None`` keeps the old whole-result-set behaviour. A *skip* past the
    end is an empty page, not an error, because the UI asks for page N without
    knowing how many pages there are.
    """
    needle = query.strip().lower()
    if not needle:
        return []
    hits = [
        note
        for note in notes
        if needle in note["title"].lower() or needle in note["body"].lower()
    ]
    hits.sort(key=lambda note: note["title"])
    start = max(int(skip), 0)
    if limit is None:
        return hits[start:]
    return hits[start : start + max(int(limit), 0)]
