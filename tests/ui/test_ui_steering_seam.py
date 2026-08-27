"""Where a mid-turn keystroke goes, and who is allowed to lose it.

Three seams, all offline and all pure over injected callables — no model, no Textual:

* the app's submit handler must route a message to *steering* while a turn is running and
  to *submissions* when one is not, because those are two different things (continue the
  turn vs. start a new one);
* the screen must follow the orchestrator's pending list *down* as well as up, since the
  loop takes corrections at moments the app cannot see;
* ``multi_turn_events`` must catch the two cases where a steer has no turn left to join —
  the turn ended between the keystroke and the loop's next step, or the turn was
  interrupted — instead of leaving the message in the holder forever.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from ronin.core.steering import Steering
from ronin.core.types import Event, TextDelta
from ronin.ui.app import multi_turn_events
from ronin.ui.reduce import ViewState

# --------------------------------------------------------------------------- #
# The view state follows the orchestrator's list, in both directions
# --------------------------------------------------------------------------- #


def test_the_pending_list_can_go_down_as_well_as_up() -> None:
    # `with_queued` appends; nothing could shrink the list. Once the orchestrator owns the
    # real queue the screen has to be able to follow it emptying, which is what happens
    # the moment the loop takes a correction.
    state = ViewState().with_queue(("one", "two"))
    assert state.queued == ("one", "two")

    assert state.with_queue(("two",)).queued == ("two",)
    assert state.with_queue(()).queued == ()


def test_following_a_list_that_did_not_move_keeps_the_same_state_object() -> None:
    # Pulled on every event of a long turn, so an unchanged list must not rebuild the
    # state hundreds of times.
    state = ViewState().with_queue(("one",))
    assert state.with_queue(("one",)) is state
    assert state.with_queue(["one"]) is state


def test_a_steering_holder_and_the_view_agree_on_what_is_waiting() -> None:
    steering = Steering()
    steering.push("use pathlib")
    steering.push("")

    assert ViewState().with_queue(steering.pending()).queued == ("use pathlib",)
    steering.drain()
    assert ViewState().with_queue(steering.pending()).queued == ()


# --------------------------------------------------------------------------- #
# multi_turn_events — the safety net under the steering channel
# --------------------------------------------------------------------------- #


async def _reply_turn(prompt: str, seen: list[str]) -> AsyncIterator[Event]:
    seen.append(prompt)
    yield TextDelta(text=f"reply to {prompt}")


async def test_a_correction_with_no_turn_left_to_join_becomes_the_next_turn() -> None:
    # The race the steering channel cannot win on its own: the turn ended between the
    # keystroke and the loop's next iteration. Without this the message is held by a
    # session that has stopped asking for it.
    seen: list[str] = []
    steering = Steering()
    steering.push("you missed the point")
    submissions: asyncio.Queue[str | None] = asyncio.Queue()
    submissions.put_nowait(None)

    events = [
        event
        async for event in multi_turn_events(
            "first", submissions, lambda p: _reply_turn(p, seen), leftover=steering.drain
        )
    ]

    assert seen == ["first", "you missed the point"]
    assert [e.text for e in events if isinstance(e, TextDelta)] == [
        "reply to first",
        "reply to you missed the point",
    ]
    assert steering.pending() == ()


async def test_several_leftover_corrections_become_one_turn_and_not_several() -> None:
    # They were typed as one thought about the same work. Delivering them as separate
    # turns would let the model answer the first before it could see the second.
    seen: list[str] = []
    steering = Steering()
    steering.push("use pathlib")
    steering.push("and no new deps")
    submissions: asyncio.Queue[str | None] = asyncio.Queue()
    submissions.put_nowait(None)

    async for _event in multi_turn_events(
        "first", submissions, lambda p: _reply_turn(p, seen), leftover=steering.drain
    ):
        pass

    assert seen == ["first", "use pathlib\n\nand no new deps"]


async def test_an_empty_holder_leaves_the_driver_waiting_on_the_queue_as_before() -> None:
    # The net must not turn into a spin: an empty drain falls through to the queue, and
    # the driver still ends on a queued None.
    seen: list[str] = []
    steering = Steering()
    submissions: asyncio.Queue[str | None] = asyncio.Queue()
    submissions.put_nowait("second")
    submissions.put_nowait(None)

    async for _event in multi_turn_events(
        "first", submissions, lambda p: _reply_turn(p, seen), leftover=steering.drain
    ):
        pass

    assert seen == ["first", "second"]


async def test_a_correction_typed_during_the_leftover_turn_is_picked_up_too() -> None:
    # The net is drained after *every* turn, including one it started itself.
    seen: list[str] = []
    steering = Steering()
    steering.push("one")
    submissions: asyncio.Queue[str | None] = asyncio.Queue()
    submissions.put_nowait(None)

    def run(prompt: str) -> AsyncIterator[Event]:
        if prompt == "one":
            steering.push("two")
        return _reply_turn(prompt, seen)

    async for _event in multi_turn_events("first", submissions, run, leftover=steering.drain):
        pass

    assert seen == ["first", "one", "two"]


async def test_without_a_leftover_channel_the_driver_is_exactly_what_it_was() -> None:
    seen: list[str] = []
    submissions: asyncio.Queue[str | None] = asyncio.Queue()
    submissions.put_nowait("second")
    submissions.put_nowait(None)

    async for _event in multi_turn_events("first", submissions, lambda p: _reply_turn(p, seen)):
        pass

    assert seen == ["first", "second"]


def test_the_driver_asks_for_the_leftovers_only_between_turns() -> None:
    # A guard on the shape of the thing: `leftover` is a drain, so calling it during a
    # turn would take a message the loop was about to inject. It is only ever called
    # where the previous turn's events have run out.
    import ast
    import inspect

    from ronin.ui import app as app_module

    source = inspect.getsource(app_module.multi_turn_events)
    tree = ast.parse(inspect.cleandoc(source))
    (function,) = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]
    (loop,) = [node for node in function.body if isinstance(node, ast.While)]
    calls: list[str] = []
    for statement in loop.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls.append(node.func.id)
    # Called once, in the while body — not inside the `async for` that drains the turn.
    assert calls.count("leftover") == 1
    (async_for,) = [node for node in loop.body if isinstance(node, ast.AsyncFor)]
    inner = [
        node.func.id
        for statement in async_for.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "leftover" not in inner


def _sequence_typed(items: Sequence[str]) -> tuple[str, ...]:
    """`with_queue` takes any Sequence, so the orchestrator may hand it a list."""
    return ViewState().with_queue(items).queued


def test_the_pull_accepts_any_sequence_the_orchestrator_hands_it() -> None:
    assert _sequence_typed(["a", "b"]) == ("a", "b")
    assert _sequence_typed(("a",)) == ("a",)


# --------------------------------------------------------------------------- #
# The CLI wiring: one holder, three seams
# --------------------------------------------------------------------------- #


def test_the_screen_and_the_loop_are_wired_to_the_same_holder() -> None:
    """The failure this guards against is silent and total: wire the app to one
    ``Steering`` and the loop to another and everything still runs — messages are
    accepted, shown, and never delivered. There is no assertion inside a turn that would
    notice, so the wiring itself is what gets pinned.

    Structural rather than behavioural because ``_app_session`` needs a whole assembled
    ``Agent``; the behaviour it wires is covered end to end in ``tests/cli`` against a
    real ``Runtime``.
    """
    import ast
    import inspect

    from ronin.cli import main as cli_main

    tree = ast.parse(inspect.getsource(cli_main._app_session))

    # The holder is taken from the runtime — the same object `Conversation._turn` hands
    # to the loop — and not constructed here.
    assigned = {
        ast.unparse(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "steering"
    }
    assert assigned == {"agent.runtime.steering"}

    seams = {
        keyword.arg: ast.unparse(keyword.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg in {"on_steer", "on_steering", "leftover"}
    }
    assert seams == {
        "on_steer": "steering.push",
        "on_steering": "steering.pending",
        "leftover": "steering.drain",
    }
