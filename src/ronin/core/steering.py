"""Mid-turn corrections, held until the loop reaches a point where they can land.

A coding agent is wrong for whole minutes at a time, and the moment you *know* it is
wrong is the moment it starts down the wrong path — not the moment it finishes. Before
this, a message typed then was queued: the agent kept working for another two minutes on
work you had already rejected, and your correction ran afterwards as a fresh turn against
a transcript full of the thing you were trying to stop. The only escape was ``esc``,
which throws away the good part of the turn along with the bad.

Steering is the middle option: the message joins the *running* conversation at the next
safe seam, so the model's very next decision already accounts for it.

**Where the seam is, and why.** A steer is appended at the top of a loop iteration —
after every tool result for the previous assistant message has been written, before the
next model call. That is not a performance compromise, it is the only correct place:
provider APIs require every ``tool_use`` to be answered by a ``tool_result``, so slipping
a user message between a tool call and its result would make the transcript invalid.
Landing at the iteration boundary is also what makes the behaviour predictable — a steer
takes effect at the model's next decision, always, rather than "somewhere in the next few
seconds depending on which tool happened to be running".

**A steer does not cancel in-flight work.** The running tool finishes and its result is
kept. Stopping work is what ``esc`` is for, and conflating the two would make every
correction a gamble about how much progress it costs. Intent-based cancellation ("this
steer obviously invalidates the edit you are mid-way through") is a real feature and is
deliberately not this one.

The holder is mutable and deliberately tiny: the loop is handed :meth:`Steering.drain`
and never sees the object, the UI is handed :meth:`Steering.push` and
:meth:`Steering.pending` and never sees it either. Nothing here knows what a terminal or
a provider is.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Steering"]


@dataclass(slots=True)
class Steering:
    """Messages typed mid-turn, waiting for the loop's next injection point.

    Mutable because a keystroke *is* a state change and the two sides of it run in
    different tasks: the UI pushes from the event pump, the loop drains from the turn
    worker. Both are on the same event loop, and every method here completes without
    awaiting, so a drain can never interleave with a push.
    """

    _pending: list[str] = field(default_factory=list, repr=False)

    def push(self, text: str) -> None:
        """Hold a correction for the next injection point.

        Blank input is not a correction and is dropped here rather than at the call site,
        so every caller gets the same answer to "does whitespace steer?". Returns nothing
        on purpose: the answer to "did it land" is :meth:`pending`, which the screen has
        to read anyway, and a bool here would be a second source of truth for it.
        """
        if not text.strip():
            return
        self._pending.append(text)

    def drain(self) -> tuple[str, ...]:
        """Take everything waiting, in the order it was typed, and forget it.

        All of it, not one: someone who types two corrections during one long turn meant
        both, and delivering them one iteration apart would let the model act on the
        first while the second was still invisible.
        """
        taken = tuple(self._pending)
        self._pending.clear()
        return taken

    def pending(self) -> tuple[str, ...]:
        """What is waiting, without taking it. The screen's read of the same state.

        A separate method rather than a field so that showing the queue can never empty
        it — the UI pulls this on every event.
        """
        return tuple(self._pending)
