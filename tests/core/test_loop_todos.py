"""The plan on the way out: ``AgentState.todos`` is filled from the live tool context.

The loop cannot see the plan — ``todo_write`` writes it into a ``ToolContext`` that the
``ToolRegistry`` protocol does not expose — so before this it was empty on every state
the loop produced. The consequences were all downstream and all silent: the transcript
recorded no plan, and a session resumed from it came back with an empty checklist and a
model that had just been told nothing was in progress.

So it is pulled, exactly like ``policy.cancelled()`` and the steering channel. These
tests are about *when* it is pulled, because a snapshot taken at the wrong moment is a
plan one turn out of date rather than an obviously missing one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fakes import (
    FakeModel,
    FakePolicy,
    FakeTools,
    call,
    seed,
    spec,
    text_turn,
    tool_turn,
)

from ronin.core.loop import StalledError, run_turn
from ronin.core.types import Event, Todo, TodoStatus, ToolResult, TurnEnd

FIRST = (Todo(id="1", subject="read the test", status=TodoStatus.IN_PROGRESS),)
SECOND = (
    Todo(id="1", subject="read the test", status=TodoStatus.COMPLETED),
    Todo(id="2", subject="fix the bug", status=TodoStatus.IN_PROGRESS),
)


async def drive(stream: Any) -> list[Event]:
    return [event async for event in stream]


def ended(events: Sequence[Event]) -> TurnEnd:
    ends = [event for event in events if isinstance(event, TurnEnd)]
    assert ends, "the turn produced no TurnEnd"
    return ends[-1]


def plan_of(events: Sequence[Event]) -> tuple[Todo, ...]:
    state = ended(events).agent_state
    assert state is not None
    return state.todos


async def test_the_plan_rides_out_on_the_state_the_turn_ends_with() -> None:
    events = await drive(
        run_turn(
            seed(), FakeModel([text_turn("done")]), FakeTools({}), FakePolicy(), todos=lambda: FIRST
        )
    )

    assert plan_of(events) == FIRST


async def test_without_the_channel_the_state_is_exactly_what_it_was() -> None:
    events = await drive(
        run_turn(seed(), FakeModel([text_turn("done")]), FakeTools({}), FakePolicy())
    )

    assert plan_of(events) == ()


async def test_the_plan_is_read_at_the_end_not_at_the_start() -> None:
    """The reason it is a callable. A tool that rewrites the plan mid-turn must be
    reflected in the state the turn ends with — a value passed in would snapshot the
    plan before the tools that change it have run."""
    live: list[tuple[Todo, ...]] = [FIRST]

    def handle(arguments: Mapping[str, Any]) -> ToolResult:
        del arguments
        live[0] = SECOND
        return ToolResult(ok=True, content="planned")

    events = await drive(
        run_turn(
            seed(),
            FakeModel([tool_turn(call("t1", "plan")), text_turn("done")]),
            FakeTools({"plan": (spec("plan"), handle)}),
            FakePolicy(),
            todos=lambda: live[0],
        )
    )

    assert plan_of(events) == SECOND


async def test_an_interrupted_turn_still_carries_the_plan() -> None:
    # Every stop condition, not just the happy one: an interrupt is exactly when the
    # state has to be resumable, and a plan is part of what resuming means.
    policy = FakePolicy()
    policy.cancel()
    events = await drive(
        run_turn(
            seed(), FakeModel([text_turn("unreached")]), FakeTools({}), policy, todos=lambda: FIRST
        )
    )

    assert ended(events).stop_reason == "interrupted"
    assert plan_of(events) == FIRST


async def test_a_budget_stop_still_carries_the_plan() -> None:
    state = seed()
    events = await drive(
        run_turn(
            state,
            FakeModel([text_turn("unreached")]),
            FakeTools({}),
            FakePolicy(budget_reason="token_budget"),
            todos=lambda: FIRST,
        )
    )

    assert ended(events).stop_reason == "token_budget"
    assert plan_of(events) == FIRST


async def test_a_stall_abort_carries_the_plan_too() -> None:
    """A stall raises rather than ending, and the state it carries is the checkpoint a
    session resumes from — so the plan has to be on it."""
    tools = FakeTools({"read": (spec("read"), lambda _a: ToolResult(ok=True, content="x"))})
    model = FakeModel([tool_turn(call(f"t{n}", "read", path="a.py")) for n in range(8)])

    raised: list[StalledError] = []
    try:
        await drive(run_turn(seed(), model, tools, FakePolicy(), todos=lambda: FIRST))
    except StalledError as exc:
        raised.append(exc)

    assert raised, "the fixture must actually stall for this to mean anything"
    assert raised[0].agent_state is not None
    assert raised[0].agent_state.todos == FIRST


async def test_the_rest_of_the_state_still_advances_alongside_it() -> None:
    # Guards against the plan being threaded in by *replacing* the state rather than
    # adding to it: the transcript and the fields the turn started with must survive.
    start = seed()
    events = await drive(
        run_turn(
            start,
            FakeModel([text_turn("done")]),
            FakeTools({}),
            FakePolicy(),
            todos=lambda: FIRST,
        )
    )

    state = ended(events).agent_state
    assert state is not None
    assert state.todos == FIRST
    assert len(state.messages) > len(start.messages), "the assistant reply is still there"
    assert state.cwd == start.cwd
    assert state.mode == start.mode
