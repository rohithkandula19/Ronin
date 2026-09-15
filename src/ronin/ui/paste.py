"""Multi-line pastes: holding them aside so the prompt line does not eat them.

The prompt is a single-line ``Input``, and Textual's handler for a paste is::

    line = event.text.splitlines()[0]

So pasting a forty-line traceback puts *one* line in the box and drops thirty-nine on
the floor. No warning, no marker, no bell. The person sees a short line, presses enter,
and the model answers a question nobody asked. That is the bug this module exists for:
not that a pasted newline submits early — Textual already coalesces a bracketed paste
into one event and never submits on it — but that the text is silently lost.

A paste of two or more lines is **stashed** here and a short marker goes in the box
instead. On submit the marker expands back to the exact text that was pasted. The line
stays one line, so history, the ``@file`` picker and the arrow keys keep working
unchanged, and nothing is thrown away.

Everything is pure. A ``PasteBook`` is a value, ``stash`` returns a new one, and the
module never learns what a terminal or a widget is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Below this, a paste goes straight into the line as it always has. A one-line paste
#: *is* what the box holds, so hiding it behind a marker would be ceremony for nothing.
PASTE_THRESHOLD_LINES: Final = 2


@dataclass(frozen=True, slots=True)
class Pasted:
    """One stashed paste: the marker standing in for it, and the text it stands for."""

    token: str
    text: str


@dataclass(frozen=True, slots=True)
class PasteBook:
    """Every paste stashed since the last submit, in the order they arrived."""

    items: tuple[Pasted, ...] = ()

    @property
    def lines(self) -> int:
        """Total lines held. What a "1 paste attached (42 lines)" notice reports."""
        return sum(_line_count(item.text) for item in self.items)


#: A book with nothing in it. Shared because it is immutable, in the spirit of
#: ``NO_COMPLETION`` next door.
NO_PASTES: Final = PasteBook()


def _line_count(text: str) -> int:
    return len(text.splitlines())


def token_for(number: int, lines: int) -> str:
    """The marker for paste ``number``.

    Says what it is holding, because the marker is the only thing the person can see
    before they press enter and it has to be enough to tell them nothing was lost.

    Square brackets read naturally in a prompt and survive rendering: every renderer
    puts model-derived text through ``styles.text``, which *escapes* markup rather than
    interpreting it, so a marker reaches the screen as written. (The ``[y]es`` hint in
    ``render_approval`` was eaten once by exactly that markup parsing, which is why the
    escaping is there now.)
    """
    return f"[#{number} pasted {lines} lines]"


def stash(book: PasteBook, text: str) -> tuple[PasteBook, str]:
    """Hold ``text`` aside; return the updated book and the marker to type in its place.

    A paste short enough to belong in the box comes back unchanged with the book
    untouched, so the caller can insert the return value either way and not branch.
    """
    lines = _line_count(text)
    if lines < PASTE_THRESHOLD_LINES:
        return book, text
    token = token_for(len(book.items) + 1, lines)
    return PasteBook((*book.items, Pasted(token, text))), token


def expand(line: str, book: PasteBook) -> str:
    """Put every stashed paste back where its marker sits.

    Only markers this book issued are replaced. Anything else shaped like one — text a
    person typed by hand, a marker they half-deleted, a marker from a book already
    cleared — is left exactly as written, because rewriting words someone typed is a
    worse failure than showing them a stray bracket.
    """
    for item in book.items:
        line = line.replace(item.token, item.text)
    return line


__all__ = [
    "NO_PASTES",
    "PASTE_THRESHOLD_LINES",
    "PasteBook",
    "Pasted",
    "expand",
    "stash",
    "token_for",
]
