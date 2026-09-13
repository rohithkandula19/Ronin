"""One event stream, many watchers — without the slowest one holding up the turn.

``Conversation.run_prompt`` yields an ``AsyncIterator[Event]``, and an async generator
has exactly one consumer: whoever is iterating it. That is the right shape for the
terminal, and it is the reason a session could not be *watched*. Anything that wanted a
second look at the same turn — a second pane, a browser, a phone on a train — had to
either take the stream away from the renderer or re-run the turn.

:class:`EventHub` is the missing piece: the renderer iterates the loop and publishes
each event here, and every other watcher subscribes. Four properties, and each one is a
bug somewhere if it is missing.

**A watcher can never stall the turn.** :meth:`EventHub.publish` does not wait for
anybody. It appends to a bounded ring and wakes whoever is waiting; a subscriber that
has stopped reading — a closed laptop, a dropped connection, a paused debugger — falls
behind, and the ring overwrites what it has not read. The alternative, a queue per
subscriber that the producer blocks on, means a phone that went into a tunnel freezes
the terminal. No consumer of a *session* is important enough for that.

**What was lost is named, never silently skipped.** A subscriber that falls off the back
of the ring is told how many events it missed, on the very next one it receives
(:attr:`Delivery.missed`). It is in the delivery rather than on the side because a gap a
consumer has to remember to ask about is a gap it will render as continuity — and an
event stream with a hole in it is worse than one that admits to it, since a watcher will
happily draw a turn that never ended.

**A late watcher gets context, not a fragment.** Subscribing with ``since=0`` replays
everything the ring still holds, so a browser that connects mid-turn sees the
``TurnStart`` and the tool calls that led here rather than opening on a half-finished
sentence. ``since=<seq>`` is resumption: hand back the last sequence number you saw and
the stream continues from there, which is exactly what an SSE client's
``Last-Event-ID`` header carries.

**Watchers may live on threads, not only in the loop.** The obvious consumer is a
stdlib :class:`http.server.ThreadingHTTPServer` handler, which is a thread with no event
loop of its own, so :class:`Subscription` offers both an ``async for`` and a blocking
:meth:`Subscription.blocking` iterator over the same cursor. The state is guarded by a
``threading.Lock`` for that reason: uncontended, held only for list and deque
operations, and never across an ``await``.

Nothing here knows what an HTTP response, a terminal or a provider is. It is a ring, a
cursor and two ways to wait.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .types import Event

__all__ = ["DEFAULT_CAPACITY", "Delivery", "EventHub", "Subscription"]

#: How many events the ring holds. A turn that calls twenty tools emits a few hundred
#: events, so this is roughly "the last several turns" — enough that a watcher which
#: reconnects after a lost minute still resumes without a gap, and small enough that an
#: unattended session does not grow a transcript in memory a second time. A caller that
#: knows better passes its own; the transcript on disk is the durable record either way.
DEFAULT_CAPACITY = 2048


@dataclass(frozen=True, slots=True)
class Delivery:
    """One event, its sequence number, and what was lost getting to it.

    ``missed`` is almost always ``0``. When it is not, this delivery follows a gap of
    that many events which the ring overwrote before this subscriber read them — the
    subscriber was too slow, or was not connected. Reported here rather than on the
    subscription because a consumer that must remember to check a counter is a consumer
    that will render the gap as if nothing happened.
    """

    seq: int
    event: Event
    missed: int = 0


class EventHub:
    """A bounded ring of events with any number of independent cursors over it.

    Publishing never blocks and never raises for a subscriber's sake. Closing ends
    every subscription *after* it has drained what it can still reach, so a watcher
    sees the end of the turn it was watching rather than a disconnect.
    """

    __slots__ = ("_capacity", "_closed", "_lock", "_next_seq", "_ring", "_waiters")

    def __init__(self, *, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("EventHub capacity must be at least 1")
        self._capacity = capacity
        self._ring: deque[Delivery] = deque(maxlen=capacity)
        self._next_seq = 1
        self._closed = False
        # One lock for the ring and the waiter lists. A `threading.Condition` over the
        # same lock wakes blocking watchers; loop watchers are woken through their own
        # loop, so both kinds can wait on one publish without either learning about the
        # other.
        self._lock = threading.Lock()
        self._waiters = _Waiters(self._lock)

    # ----------------------------------------------------------- the producer

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def publish(self, event: Event) -> int:
        """Append ``event`` and wake every waiter. Returns its sequence number.

        Publishing to a closed hub is refused rather than ignored: a stream that has
        ended and then emits is a bug in the producer, and swallowing it would leave a
        watcher's view and the transcript disagreeing about where the turn stopped.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("EventHub is closed; nothing more will be published")
            seq = self._next_seq
            self._next_seq += 1
            self._ring.append(Delivery(seq=seq, event=event))
        self._waiters.wake()
        return seq

    def close(self) -> None:
        """End the stream. Idempotent, and safe to call from any thread."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._waiters.wake()

    async def drain(self, events: AsyncIterator[Event]) -> None:
        """Publish everything ``events`` yields, then close. The usual producer.

        A convenience with one real job: ``close`` runs in a ``finally``, so a turn that
        raises still ends its watchers' streams instead of leaving them waiting on a
        producer that is never coming back.
        """
        try:
            async for event in events:
                self.publish(event)
        finally:
            self.close()

    # --------------------------------------------------------- the subscribers

    def subscribe(self, *, since: int | None = None) -> Subscription:
        """A cursor over this hub.

        ``since=None`` starts at the present: only events published from now on.
        ``since=0`` replays everything the ring still holds, which is what a watcher
        joining a turn in progress wants. ``since=n`` resumes after sequence ``n`` —
        hand back the last one you saw, as an SSE client does with ``Last-Event-ID``.
        """
        with self._lock:
            cursor = self._next_seq - 1 if since is None else max(0, since)
        return Subscription(self, cursor)

    # Internal: one non-blocking read. Returns the deliveries at or after `cursor`,
    # the new cursor, and whether the hub has closed — all three under one lock so a
    # subscriber cannot observe a close that happened between reading and checking.
    def _read(self, cursor: int) -> tuple[list[Delivery], int, bool]:
        with self._lock:
            if not self._ring:
                return [], cursor, self._closed
            oldest = self._ring[0].seq
            ready = [item for item in self._ring if item.seq > cursor]
            if not ready:
                return [], cursor, self._closed
            missed = max(0, oldest - cursor - 1)
            if missed:
                # Named on the first delivery after the gap. The subscriber learns what
                # it lost at the moment it could otherwise start believing it lost
                # nothing.
                head = ready[0]
                ready[0] = Delivery(seq=head.seq, event=head.event, missed=missed)
            return ready, ready[-1].seq, self._closed


class _Waiters:
    """Everyone waiting for the next publish, in whichever way they wait.

    Threads wait on a ``Condition``; event loops wait on a future which is resolved
    through their own loop, because a future may only be touched from the loop that
    owns it. Both lists are guarded by the hub's lock, so one ``wake()`` releases every
    waiter of both kinds without either knowing the other exists.
    """

    __slots__ = ("_futures", "_lock", "_ready")

    def __init__(self, lock: threading.Lock) -> None:
        self._lock = lock
        self._ready = threading.Condition(lock)
        self._futures: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = []

    def wake(self) -> None:
        with self._lock:
            self._ready.notify_all()
            pending, self._futures = self._futures, []
        for loop, future in pending:
            try:
                loop.call_soon_threadsafe(_resolve, future)
            except RuntimeError:
                # The watcher's loop closed while it was waiting. Its own iteration
                # is already over; there is nobody left to tell.
                continue

    def wait_blocking(self, timeout: float | None) -> None:
        with self._lock:
            self._ready.wait(timeout)

    def future(self) -> asyncio.Future[None]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        with self._lock:
            self._futures.append((loop, future))
        return future

    def forget(self, future: asyncio.Future[None]) -> None:
        with self._lock:
            self._futures = [pair for pair in self._futures if pair[1] is not future]


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


class Subscription:
    """One watcher's cursor. Iterate it either way; it is the same position.

    Not reusable across both interfaces at once — a subscription is one watcher — but
    a watcher may stop iterating and resume later from :attr:`cursor`, which is the
    whole point of the sequence numbers.
    """

    __slots__ = ("_cursor", "_hub", "missed")

    def __init__(self, hub: EventHub, cursor: int) -> None:
        self._hub = hub
        self._cursor = cursor
        self.missed = 0
        """Cumulative count of events this subscription never saw. For a report."""

    @property
    def cursor(self) -> int:
        """The last sequence number delivered. Hand it back as ``since`` to resume."""
        return self._cursor

    def _take(self) -> tuple[list[Delivery], bool]:
        ready, _, closed = self._hub._read(self._cursor)
        return ready, closed

    def _hand_over(self, delivery: Delivery) -> Delivery:
        """Advance the cursor by exactly what the watcher has now received.

        Per delivery, not per read. Moving it to the end of the batch was wrong in the
        one case the sequence numbers exist for: a watcher that stops part-way through
        a batch — its socket closed, its client navigated away — and then resumes from
        :attr:`cursor` would skip the rest of that batch, silently, having been told
        nothing was missed. The cursor must mean "what I have", not "what I was
        offered".
        """
        self._cursor = delivery.seq
        self.missed += delivery.missed
        return delivery

    async def __aiter__(self) -> AsyncIterator[Delivery]:
        """Deliveries until the hub closes and everything reachable has been handed out."""
        while True:
            # Register interest *before* reading. The other order loses an event
            # published between the read and the wait, and that event is the one the
            # watcher then waits forever for — the stream appears to hang one event
            # short of the end of the turn.
            waiter = self._hub._waiters.future()
            ready, closed = self._take()
            if ready:
                self._hub._waiters.forget(waiter)
                waiter.cancel()
                for delivery in ready:
                    yield self._hand_over(delivery)
                continue
            if closed:
                self._hub._waiters.forget(waiter)
                waiter.cancel()
                return
            await waiter

    def blocking(self, *, timeout: float | None = None) -> Iterator[Delivery | None]:
        """The same stream for a watcher with no event loop — an HTTP handler thread.

        Yields ``None`` when ``timeout`` elapses with nothing new. That is not a
        filler value: a watcher on a socket has periodic work of its own — an SSE
        keep-alive, noticing the client is gone — and an iterator that only ever wakes
        for data gives it nowhere to do that but a second thread. ``timeout=None``
        never yields ``None`` and waits indefinitely.
        """
        while True:
            ready, closed = self._take()
            if ready:
                for delivery in ready:
                    yield self._hand_over(delivery)
                continue
            if closed:
                return
            self._hub._waiters.wait_blocking(timeout)
            if timeout is not None:
                # Woken or timed out — the caller cannot tell from here and does not
                # need to. Handing control back on both is what makes the keep-alive
                # possible; the next pass delivers anything that arrived.
                yield None
