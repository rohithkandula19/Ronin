"""Word frequencies, for spotting boilerplate."""

from __future__ import annotations

import re

from ..blob import Blob
from ..plugin import Plugin

WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
STOPWORDS = frozenset(
    {"the", "and", "for", "with", "that", "this", "from", "are", "was", "not", "you"}
)


class WordFreq(Plugin):
    name = "word_freq"
    summary_line = "counts words, ignoring a small stopword list"
    declares = ("histogram",)

    def histogram(self, blob: Blob) -> dict[str, int]:
        counts: dict[str, int] = {}
        for match in WORD.finditer(blob.text):
            word = match.group(0).lower()
            if word in STOPWORDS:
                continue
            counts[word] = counts.get(word, 0) + 1
        return counts


PLUGIN = WordFreq()
