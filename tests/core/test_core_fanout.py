"""The fan-out: many watchers over one turn, and what happens to the slow ones.

Every test here is about a property the terminal already had for free and a *second*
watcher does not: that the producer is never held up, that a gap is admitted rather
than papered over, that a watcher arriving late gets enough context to draw anything,
and that the end of a turn reaches everyone including the watchers that were behind.

The events are real :class:`~ronin.core.types.Event` values but their content is never
asserted — the hub is a ring and a cursor and must stay ignorant of what it carries.
No provider, no socket, no sleep longer than a scheduler tick.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import pytest

from ronin.core.fanout import DEFAULT_CAPACITY, Delivery, EventHub
from ronin.core.types import TextDelta, TurnEnd, TurnStart, TurnState


def deltas(count: int, *, start: int = 0) -> list[TextDelta]:
    return [TextDelta(text=f"chunk {index}") for index in range(start, start + count)]


def texts(deliveries: list[Delivery]) -> list[str]:
    return [delivery.event.text for delivery in deliveries if isinstance(delivery.event, TextDelta)]


async def collect(hub: EventHub, *, since: int | None = None) -> list[Delivery]:
    """Everything one subscription sees, to the end of the stream."""
    return [delivery async for delivery in hub.subscribe(since=since)]


# --------------------------------------------------------------------------- #
# the shape: sequence numbers, replay, live
# --------------------------------------------------------------------------- #


def test_sequence_numbers_start_at_one_and_do_not_repeat() -> None:
    hub = EventHub()
    assert [hub.publish(event) for event in deltas(3)] == [1, 2, 3]


async def test_a_watcher_that_was_there_first_sees_everything() -> None:
    hub = EventHub()
    watching = asyncio.ensure_future(collect(hub))
    await asyncio.sleep(0)
    for event in deltas(4):
        hub.publish(event)
    hub.close()
    assert texts(await watching) == ["chunk 0", "chunk 1", "chunk 2", "chunk 3"]


async def test_a_late_watcher_can_replay_what_the_ring_still_holds() -> None:
    """A browser that connects mid-turn must see the TurnStart, not a half sentence."""
    hub = EventHub()
    hub.publish(TurnStart(turn_index=1))
    for event in deltas(2):
        hub.publish(event)
    watching = asyncio.ensure_future(collect(hub, since=0))
    await asyncio.sleep(0)
    hub.publish(TurnEnd(turn_index=1, state=TurnState.DONE))
    hub.close()
    seen = await watching
    assert isinstance(seen[0].event, TurnStart)
    assert isinstance(seen[-1].event, TurnEnd)


async def test_subscribing_live_skips_the_backlog() -> None:
    hub = EventHub()
    for event in deltas(5):
        hub.publish(event)
    watching = asyncio.ensure_future(collect(hub))
    await asyncio.sleep(0)
    hub.publish(TextDelta(text="after"))
    hub.close()
    assert texts(await watching) == ["after"]


async def test_resuming_from_a_sequence_number_continues_where_it_stopped() -> None:
    """What an SSE client's ``Last-Event-ID`` buys: a reconnect with no gap and no repeat."""
    hub = EventHub()
    for event in deltas(4):
        hub.publish(event)
    first = hub.subscribe(since=0)
    seen = []
    async for delivery in first:
        seen.append(delivery)
        if len(seen) == 2:
            break
    resumed = asyncio.ensure_future(collect(hub, since=first.cursor))
    await asyncio.sleep(0)
    hub.close()
    assert texts(await resumed) == ["chunk 2", "chunk 3"]


async def test_the_cursor_means_what_was_received_not_what_was_offered() -> None:
    """A watcher that stops part-way through a batch must resume inside it.

    Events arrive from the ring in batches, and advancing the cursor once per batch
    rather than once per delivery loses the remainder for any watcher that stops
    early — a closed socket, a client that navigated away — while reporting nothing
    missed. The sequence numbers exist precisely for that watcher.
    """
    hub = EventHub()
    for event in deltas(6):
        hub.publish(event)
    watcher = hub.subscribe(since=0)
    async for _delivery in watcher:
        break
    assert watcher.cursor == 1


def test_a_blocking_watcher_that_stops_early_keeps_its_place_too() -> None:
    hub = EventHub()
    for event in deltas(6):
        hub.publish(event)
    watcher = hub.subscribe(since=0)
    stream = watcher.blocking()
    next(stream)
    next(stream)
    del stream
    assert watcher.cursor == 2


# --------------------------------------------------------------------------- #
# the producer is never held up
# --------------------------------------------------------------------------- #


def test_publishing_does_not_wait_for_a_subscriber_that_never_reads() -> None:
    """The whole reason this is a ring and not a queue per watcher.

    A subscriber exists, has read nothing, and will read nothing. Publishing more than
    the ring holds must still return, or a phone in a tunnel freezes the terminal.
    """
    hub = EventHub(capacity=4)
    hub.subscribe(since=0)
    for event in deltas(100):
        hub.publish(event)
    assert hub.capacity == 4


async def test_a_slow_watcher_does_not_slow_a_fast_one() -> None:
    hub = EventHub(capacity=8)
    stalled = hub.subscribe(since=0)  # never iterated
    fast = asyncio.ensure_future(collect(hub, since=0))
    await asyncio.sleep(0)
    for event in deltas(6):
        hub.publish(event)
    hub.close()
    assert len(await fast) == 6
    assert stalled.cursor == 0


# --------------------------------------------------------------------------- #
# a gap is admitted
# --------------------------------------------------------------------------- #


async def test_a_watcher_that_fell_off_the_ring_is_told_how_much_it_lost() -> None:
    """Silently resuming is the failure mode: the watcher draws a turn that never ended."""
    hub = EventHub(capacity=4)
    late = hub.subscribe(since=0)
    for event in deltas(10):
        hub.publish(event)
    hub.close()
    seen = [delivery async for delivery in late]
    # The ring kept the last four; the six before them are gone and are counted.
    assert texts(seen) == ["chunk 6", "chunk 7", "chunk 8", "chunk 9"]
    assert seen[0].missed == 6
    assert late.missed == 6


async def test_nothing_is_reported_as_missed_when_nothing_was() -> None:
    hub = EventHub(capacity=64)
    watcher = hub.subscribe(since=0)
    for event in deltas(10):
        hub.publish(event)
    hub.close()
    seen = [delivery async for delivery in watcher]
    assert all(delivery.missed == 0 for delivery in seen)
    assert watcher.missed == 0


async def test_the_gap_is_reported_on_the_delivery_not_only_on_the_side() -> None:
    """A consumer that has to remember to check a counter will render the gap as continuity."""
    hub = EventHub(capacity=2)
    late = hub.subscribe(since=0)
    for event in deltas(5):
        hub.publish(event)
    hub.close()
    first = await anext(aiter(late))
    assert first.missed == 3
    assert first.seq == 4


async def test_a_resumption_from_a_sequence_the_ring_has_dropped_names_the_gap() -> None:
    hub = EventHub(capacity=3)
    for event in deltas(20):
        hub.publish(event)
    hub.close()
    seen = [delivery async for delivery in hub.subscribe(since=1)]
    assert seen[0].missed == 16  # sequences 2..17
    assert seen[0].seq == 18


# --------------------------------------------------------------------------- #
# the end of the turn reaches everyone
# --------------------------------------------------------------------------- #


async def test_closing_ends_a_waiting_watcher() -> None:
    hub = EventHub()
    watching = asyncio.ensure_future(collect(hub))
    await asyncio.sleep(0)
    hub.close()
    assert await asyncio.wait_for(watching, timeout=2) == []


async def test_closing_still_hands_out_what_is_already_in_the_ring() -> None:
    """A watcher must see the end of the turn it was watching, not a disconnect."""
    hub = EventHub()
    hub.publish(TurnStart(turn_index=1))
    hub.publish(TurnEnd(turn_index=1, state=TurnState.DONE))
    hub.close()
    seen = [delivery async for delivery in hub.subscribe(since=0)]
    assert isinstance(seen[-1].event, TurnEnd)


def test_closing_twice_is_not_an_error() -> None:
    hub = EventHub()
    hub.close()
    hub.close()
    assert hub.closed


def test_publishing_after_close_is_refused_rather_than_dropped() -> None:
    """A producer emitting past the end of its own stream is a bug, not a no-op.

    Swallowing it leaves the watchers' view and the transcript disagreeing about where
    the turn stopped, and nothing anywhere says which is right.
    """
    hub = EventHub()
    hub.close()
    with pytest.raises(RuntimeError, match="closed"):
        hub.publish(TextDelta(text="late"))


async def test_drain_publishes_a_stream_and_closes_it() -> None:
    async def produce() -> AsyncIterator[TextDelta]:
        for event in deltas(3):
            yield event

    hub = EventHub()
    watching = asyncio.ensure_future(collect(hub))
    await asyncio.sleep(0)
    await hub.drain(produce())
    assert texts(await watching) == ["chunk 0", "chunk 1", "chunk 2"]
    assert hub.closed


async def test_a_turn_that_raises_still_ends_its_watchers() -> None:
    """Otherwise every watcher waits forever on a producer that is not coming back."""

    async def explode() -> AsyncIterator[TextDelta]:
        yield TextDelta(text="one")
        raise RuntimeError("the provider went away")

    hub = EventHub()
    watching = asyncio.ensure_future(collect(hub))
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="went away"):
        await hub.drain(explode())
    assert texts(await asyncio.wait_for(watching, timeout=2)) == ["one"]


# --------------------------------------------------------------------------- #
# no lost wakeup
# --------------------------------------------------------------------------- #


async def test_an_event_published_while_a_watcher_is_settling_is_not_lost() -> None:
    """The race the ordering inside ``__aiter__`` exists for.

    Read-then-wait loses an event published in between, and the stream then hangs one
    event short of the end of the turn — which reads as the model stopping mid-word.
    """
    hub = EventHub()
    watching = asyncio.ensure_future(collect(hub))
    for _ in range(20):
        await asyncio.sleep(0)
        hub.publish(TextDelta(text="x"))
    hub.close()
    assert len(await asyncio.wait_for(watching, timeout=2)) == 20


async def test_many_watchers_all_see_the_whole_turn() -> None:
    hub = EventHub()
    watchers = [asyncio.ensure_future(collect(hub)) for _ in range(8)]
    await asyncio.sleep(0)
    for event in deltas(25):
        hub.publish(event)
    hub.close()
    seen = await asyncio.wait_for(asyncio.gather(*watchers), timeout=5)
    assert [len(one) for one in seen] == [25] * 8


# --------------------------------------------------------------------------- #
# the watcher with no event loop
# --------------------------------------------------------------------------- #


def test_a_thread_can_watch_the_same_hub() -> None:
    """An ``http.server`` handler is a thread with no loop, and it is the first client."""
    hub = EventHub()
    seen: list[Delivery] = []
    started = threading.Event()

    def watch() -> None:
        subscription = hub.subscribe(since=0)
        started.set()
        for delivery in subscription.blocking():
            assert delivery is not None  # no timeout was asked for
            seen.append(delivery)

    thread = threading.Thread(target=watch)
    thread.start()
    started.wait(timeout=2)
    for event in deltas(5):
        hub.publish(event)
    hub.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert texts(seen) == ["chunk 0", "chunk 1", "chunk 2", "chunk 3", "chunk 4"]


def test_a_blocking_watcher_gets_control_back_on_a_timeout() -> None:
    """So a socket watcher can send its keep-alive without needing a second thread."""
    hub = EventHub()
    stream = hub.subscribe().blocking(timeout=0.01)
    assert next(stream) is None
    hub.publish(TextDelta(text="finally"))
    assert isinstance(next(stream), Delivery)
    hub.close()


def test_a_blocking_watcher_with_no_timeout_ends_when_the_hub_does() -> None:
    hub = EventHub()
    hub.publish(TextDelta(text="one"))
    hub.close()
    assert len(list(hub.subscribe(since=0).blocking())) == 1


async def test_a_loop_watcher_and_a_thread_watcher_are_woken_by_one_publish() -> None:
    hub = EventHub()
    from_thread: list[Delivery] = []
    started = threading.Event()

    def watch() -> None:
        subscription = hub.subscribe(since=0)
        started.set()
        from_thread.extend(one for one in subscription.blocking() if one is not None)

    thread = threading.Thread(target=watch)
    thread.start()
    await asyncio.to_thread(started.wait, 2)
    watching = asyncio.ensure_future(collect(hub, since=0))
    await asyncio.sleep(0)
    for event in deltas(4):
        hub.publish(event)
    hub.close()
    assert len(await asyncio.wait_for(watching, timeout=5)) == 4
    await asyncio.to_thread(thread.join, 5)
    assert len(from_thread) == 4


# --------------------------------------------------------------------------- #
# the shape of the thing itself
# --------------------------------------------------------------------------- #


def test_a_capacity_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        EventHub(capacity=0)


def test_the_default_capacity_holds_more_than_one_turn() -> None:
    """A turn calling twenty tools emits a few hundred events; a reconnect must fit."""
    assert DEFAULT_CAPACITY >= 1024


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
