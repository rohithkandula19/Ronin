"""``@file`` mentions: finding the token being typed, and ranking paths against it.

Typing a path by hand is the most common thing anyone does in a coding session and
the one the terminal helps with least: you know the file is called something like
``compaction``, you do not remember whether it is ``src/ronin/context/compaction.py``
or ``src/ronin/compaction/context.py``, and getting it wrong costs the model a failed
read. ``@compac`` and one keystroke is the whole feature.

**A mention expands to a path, not to the file's contents.** The line
``look at @app.py`` becomes ``look at src/ronin/ui/app.py`` and nothing else happens —
no read, no attachment, no token cost. The model decides whether to open it, with the
same tools and the same approval gates it always had. Attaching contents would be a
second, larger feature: it would have to decide how much of a 5000-line file to
include, what to do when the file changes later in the session, and whether the copy
in the transcript or the file on disk is the truth.

Everything here is pure. The corpus of paths arrives as a ``Sequence[str]`` from the
caller, so ranking is tested against a hand-written list of ten paths rather than
against a repo, and the module never learns what a filesystem or a terminal is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Final

#: What starts a mention.
MENTION_PREFIX: Final = "@"

#: How many candidates are offered at once. Enough to contain the answer for a
#: two-or-three-character query, few enough that the list never takes the screen away
#: from the transcript.
MENTION_LIMIT: Final = 8

#: A bare ``@`` offers nothing. Ranking 3000 paths against the empty string can only
#: return whatever sorts first, which teaches the user that the feature is broken
#: rather than that it is waiting — so it waits visibly for one character instead.
MIN_QUERY_CHARS: Final = 1

__all__ = [
    "MENTION_LIMIT",
    "MENTION_PREFIX",
    "MIN_QUERY_CHARS",
    "NO_COMPLETION",
    "Completion",
    "Mention",
    "accept",
    "active_mention",
    "rank",
]


@dataclass(frozen=True, slots=True)
class Mention:
    """The ``@`` token under the cursor: where it starts, and what follows the ``@``.

    ``end`` is exclusive and is where the token stops — the cursor is inside the token
    by construction, but the token may continue past it (someone editing the middle of
    an already-typed path), and replacing only up to the cursor would leave the tail
    behind as garbage.
    """

    start: int
    end: int
    query: str


def active_mention(text: str, cursor: int) -> Mention | None:
    """The mention the cursor is inside, or ``None``.

    A mention is a whitespace-delimited token whose *first* character is ``@``. That
    the ``@`` has to start the token is the rule that keeps ``ask@example.com`` and
    ``user@host:path`` from turning into file pickers — an ``@`` in the middle of a
    word is part of the word.
    """
    if not 0 <= cursor <= len(text):
        return None
    start = cursor
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = cursor
    while end < len(text) and not text[end].isspace():
        end += 1
    token = text[start:end]
    if not token.startswith(MENTION_PREFIX):
        return None
    return Mention(start=start, end=end, query=token[len(MENTION_PREFIX) :])


def _subsequence(query: str, candidate: str) -> bool:
    """Whether every character of ``query`` appears in ``candidate``, in order."""
    position = 0
    for char in candidate:
        if position < len(query) and char == query[position]:
            position += 1
    return position == len(query)


def _score(query: str, path: str) -> int | None:
    """How well ``path`` answers ``query``; lower is better, ``None`` is no match.

    A ladder rather than a weighted sum, because a ladder is explainable and a sum is
    not: someone who types ``app`` wants ``ui/app.py`` above ``apps/api/main.py``, and
    the reason is exactly that the first is a name match and the second is only a path
    match. Five rungs, most specific first.
    """
    name = PurePosixPath(path).name.lower()
    lowered = path.lower()
    if name.startswith(query):
        return 0
    if query in name:
        return 1
    if _subsequence(query, name):
        return 2
    if query in lowered:
        return 3
    if _subsequence(query, lowered):
        return 4
    return None


def rank(query: str, paths: Sequence[str], *, limit: int = MENTION_LIMIT) -> tuple[str, ...]:
    """The best ``limit`` paths for ``query``, best first.

    Case-insensitive. Ties inside a rung break on path length and then
    lexicographically, which makes the order total and therefore testable: the same
    query against the same corpus always produces the same list, so a shell-completion
    style "it depends on the walk order" bug cannot hide here.

    A query shorter than :data:`MIN_QUERY_CHARS` returns nothing.
    """
    lowered = query.lower()
    if len(lowered) < MIN_QUERY_CHARS:
        return ()
    scored: list[tuple[int, int, str]] = []
    for path in paths:
        score = _score(lowered, path)
        if score is not None:
            scored.append((score, len(path), path))
    scored.sort()
    return tuple(path for _score_, _length, path in scored[:limit])


@dataclass(frozen=True, slots=True)
class Completion:
    """The offered paths and which one is selected. Empty means nothing is offered.

    Frozen and pure, like every other piece of keyboard state here: what ``down`` does
    to a selection is a question a test answers without a terminal.
    """

    candidates: tuple[str, ...] = ()
    selected: int = 0

    @property
    def open(self) -> bool:
        return bool(self.candidates)

    @property
    def choice(self) -> str:
        """The path ``tab`` would take. ``""`` when nothing is offered."""
        return self.candidates[self.selected] if self.candidates else ""

    def moved(self, delta: int) -> Completion:
        """Move the selection, wrapping at both ends.

        Wrapping rather than clamping because the list is short and a wrap costs one
        keypress to undo, where a clamp leaves ``up`` silently doing nothing at the top
        — which reads as a wedged UI.
        """
        if not self.candidates:
            return self
        return replace(self, selected=(self.selected + delta) % len(self.candidates))


#: Nothing on offer. A shared immutable value rather than a fresh ``Completion()`` at
#: each default, so it can be a dataclass field default without a call.
NO_COMPLETION: Final = Completion()


def accept(text: str, cursor: int, path: str) -> tuple[str, int]:
    """Replace the mention under the cursor with ``path``. Returns ``(text, cursor)``.

    The cursor always lands where the next word would be typed: after the path and
    after exactly one space. A space already following the mention is consumed rather
    than doubled — leaving the cursor in front of it would make the next character butt
    straight against the path, which is the bug this rule exists to prevent.

    Returns the input unchanged when the cursor is not inside a mention, so a stray
    ``tab`` can never corrupt the line.
    """
    mention = active_mention(text, cursor)
    if mention is None or not path:
        return text, cursor
    tail = text[mention.end :]
    if tail.startswith(" "):
        tail = tail[1:]
    head = text[: mention.start] + path + " "
    return head + tail, len(head)
