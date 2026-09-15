"""The plan the model is working to, on screen while it works.

The audit rated this "updates only at TurnEnd". That was wrong, and the truth was
worse: nothing populated the field TurnEnd reads, so the checklist was **always empty
in a real session**. Three separate breaks, each individually invisible:

1. ``todo_write`` assigned ``ctx.todos``, and the live loop passes ``on_output`` for
   every call — so every tool runs against a shallow ``replace()`` copy of the context
   and the assignment landed on a throwaway.
2. ``ToolContext.todos`` had one writer and zero readers, despite its own docstring
   saying "read by the UI".
3. ``AgentState.todos`` was never populated outside a resumed session, so the reducer's
   ``TurnEnd`` branch read an empty tuple — which, once (1) and (2) were fixed, would
   have wiped a live plan the instant the turn ended.

Each of the three has a test here, because each was green before and would be green
again alone.

(3) was closed only halfway at the time: the reducer stopped *wiping* the plan, but
nothing filled ``AgentState.todos``, so the plan still never reached the transcript and
a resumed session came back with an empty checklist. The loop now pulls it at every
state advance and the conversation carries it, which makes the plan a round trip — out
through ``TurnEnd.agent_state``, back in through :func:`seed_todos`. Section 4 covers
the return leg.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import ClassVar

import pytest
from ui_harness import happy_turn, stream

from ronin.cli.gate import live_todos, seed_todos
from ronin.core.types import AgentState, Budget, Todo, TodoStatus, ToolUse, TurnEnd, TurnState
from ronin.tools.base import ToolContext
from ronin.tools.registry import ToolRegistry
from ronin.tools.task import TodoWriteTool
from ronin.ui.reduce import ViewState, reduce_event

PLAN = [
    {"content": "read the failing test", "status": "completed"},
    {"content": "fix the off-by-one", "status": "in_progress"},
    {"content": "run the suite", "status": "pending"},
]


def registry(root: Path) -> ToolRegistry:
    return ToolRegistry((TodoWriteTool(),), ToolContext(root=root))


def write_plan() -> ToolUse:
    return ToolUse(id="c1", name="todo_write", arguments={"todos": PLAN})


# --------------------------------------------------------------------------- #
# 1. the plan must survive the shallow copy the streaming path makes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("streaming", [False, True])
async def test_the_plan_reaches_the_shared_context_on_either_path(streaming: bool) -> None:
    """The break that made every other part of this moot.

    ``ToolRegistry._execute`` binds ``on_output`` onto a *copy* of the context so two
    concurrent calls cannot post chunks to each other's line. The copy is shallow, which
    is why ``read_files`` (a set, mutated) survives it and a rebound field does not —
    and ``core.loop`` passes ``on_output`` for **every** call, so the streaming path is
    not an edge case, it is the only path a real turn takes.
    """
    with tempfile.TemporaryDirectory() as directory:
        reg = registry(Path(directory))

        if streaming:
            result = await reg.execute_streaming(write_plan(), lambda _chunk: None)
        else:
            result = await reg.execute(write_plan())

        assert result.ok
        assert len(reg.ctx.todos) == 3, "the plan was written to a throwaway context"


async def test_set_todos_replaces_rather_than_appending() -> None:
    """A second plan is the plan, not the plan plus the old one.

    In-place mutation is what makes the copy work, and the obvious in-place mutation —
    ``extend`` — would grow the list forever as the model revises its plan.
    """
    with tempfile.TemporaryDirectory() as directory:
        reg = registry(Path(directory))
        await reg.execute_streaming(write_plan(), lambda _chunk: None)

        # `in_progress` on purpose: `parse_todos` refuses a plan with work remaining and
        # nothing started, which is a rule about honesty in the panel, not a quirk.
        second = await reg.execute_streaming(
            ToolUse(
                id="c2",
                name="todo_write",
                arguments={"todos": [{"content": "one thing left", "status": "in_progress"}]},
            ),
            lambda _chunk: None,
        )

        assert second.ok, second.error
        assert [todo.subject for todo in reg.ctx.todos] == ["one thing left"]


# --------------------------------------------------------------------------- #
# 2. the UI can actually read it
# --------------------------------------------------------------------------- #


async def test_live_todos_reads_through_the_gated_registry() -> None:
    """The plan sits two hops from the caller: gate → inner registry → context."""
    with tempfile.TemporaryDirectory() as directory:
        reg = registry(Path(directory))
        await reg.execute_streaming(write_plan(), lambda _chunk: None)

        class Gate:
            inner = reg

        assert [todo.subject for todo in live_todos(Gate())] == [
            "read the failing test",
            "fix the off-by-one",
            "run the suite",
        ]


@pytest.mark.parametrize(
    "registry_like",
    [object(), type("NoCtx", (), {"inner": object()})()],
    ids=["no inner", "inner without a context"],
)
def test_live_todos_degrades_to_no_plan_rather_than_raising(registry_like: object) -> None:
    """Both hops are optional by design. A registry assembled without a context must
    read as "no plan" — a status pane is not worth an exception."""
    assert live_todos(registry_like) == ()


def test_live_todos_renders_only_what_it_can_render() -> None:
    """``ToolContext.todos`` is typed ``list[Any]`` because it belongs to whichever tool
    owns the plan. The UI takes the entries it understands and ignores the rest."""

    class Ctx:
        todos: ClassVar[list[object]] = [
            Todo(id="1", subject="real", status=TodoStatus.PENDING),
            "not a todo",
            7,
        ]

    class Gate:
        inner = type("Inner", (), {"ctx": Ctx()})()

    assert [todo.subject for todo in live_todos(Gate())] == ["real"]


# --------------------------------------------------------------------------- #
# 3. the end of a turn must not wipe the plan
# --------------------------------------------------------------------------- #


def turn_end(todos: tuple[Todo, ...]) -> TurnEnd:
    return TurnEnd(
        turn_index=0,
        state=TurnState.DONE,
        stop_reason="end_turn",
        agent_state=AgentState(messages=(), budget=Budget(), todos=todos),
    )


def test_the_end_of_a_turn_does_not_wipe_a_plan_pushed_in_live() -> None:
    """The break that fixing the other two would have created.

    ``AgentState.todos`` is empty on every live turn — nothing populates it outside a
    resumed session — so reading it unconditionally at ``TurnEnd`` would clear the
    checklist exactly when the user looks up to see what happened.
    """
    live = (Todo(id="1", subject="fix the off-by-one", status=TodoStatus.IN_PROGRESS),)
    state = ViewState().with_todos(live)

    ended = reduce_event(state, turn_end(()))

    assert ended.todos == live, "the turn's end cleared the plan"


def test_a_turn_that_carries_its_own_plan_still_wins() -> None:
    """A resumed session *does* arrive with todos in its state, and that is the
    authority when present — the fallback is for the empty case only."""
    resumed = (Todo(id="9", subject="from the saved session", status=TodoStatus.PENDING),)
    state = ViewState().with_todos((Todo(id="1", subject="stale", status=TodoStatus.COMPLETED),))

    ended = reduce_event(state, turn_end(resumed))

    assert ended.todos == resumed


# --------------------------------------------------------------------------- #
# the app asks for it
# --------------------------------------------------------------------------- #


async def test_the_app_paints_a_plan_the_stream_never_carried() -> None:
    """End to end through the real app, and the assertion is *which source won*.

    ``happy_turn()``'s ``TurnEnd`` carries no todos — like every live turn. So a plan on
    screen after the stream has drained can only have come from ``on_todos``, which is
    both halves of this item at once: the app asks, and the turn's end does not wipe the
    answer.
    """
    pytest.importorskip("textual", reason="the interactive TUI needs the 'tui' extra")
    from textual.widgets import Static

    from ronin.ui.app import TODOS_ID, Session, _build_app

    asked: list[int] = []

    def plan() -> tuple[Todo, ...]:
        asked.append(1)
        return (Todo(id="1", subject="an in-flight step", status=TodoStatus.IN_PROGRESS),)

    app = _build_app(Session(events=stream(happy_turn()), on_todos=plan))
    async with app.run_test() as pilot:
        await pilot.pause()
        shown = str(app.query_one(f"#{TODOS_ID}", Static).visual)

    assert asked, "the app never asked for the plan"
    assert "an in-flight step" in shown


async def test_the_app_does_not_rebuild_its_state_for_an_unchanged_plan() -> None:
    """Asked on every event, so an unchanged answer must be cheap.

    A long turn is mostly events that say nothing about the plan; rebuilding the view
    state for each would churn hundreds of objects per turn for no visible change.
    """
    pytest.importorskip("textual", reason="the interactive TUI needs the 'tui' extra")
    from ronin.ui.app import Session, _build_app

    fixed = (Todo(id="1", subject="steady", status=TodoStatus.IN_PROGRESS),)
    app = _build_app(Session(events=stream(happy_turn()), on_todos=lambda: fixed))
    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.state.todos
        app._refresh_todos()
        app._refresh_todos()

    assert app.state.todos is first, "an unchanged plan replaced the state anyway"


# --------------------------------------------------------------------------- #
# 4. the return leg: a resumed plan gets back into the live context
# --------------------------------------------------------------------------- #


def test_seed_todos_writes_where_live_todos_reads() -> None:
    """The round trip in one assertion. A plan restored from an ``AgentState`` has to
    land in the same place ``todo_write`` writes to, or the checklist stays empty and
    the model is told nothing is in progress."""
    with tempfile.TemporaryDirectory() as directory:
        reg = registry(Path(directory))

        class Gate:
            inner = reg

        restored = (Todo(id="1", subject="carried over", status=TodoStatus.IN_PROGRESS),)
        assert seed_todos(Gate(), restored) is True
        assert [todo.subject for todo in live_todos(Gate())] == ["carried over"]


async def test_a_seeded_plan_survives_the_shallow_copy_every_tool_gets() -> None:
    """The trap that made break (1) invisible, from the other direction.

    The loop hands every tool a ``replace()`` copy of the context, so a *rebound* list
    is written to a throwaway while one mutated in place is shared. Seeding goes through
    ``set_todos`` for exactly this reason — assigning the attribute would look correct
    here and be invisible to every tool.
    """
    from dataclasses import replace as copy_context

    with tempfile.TemporaryDirectory() as directory:
        reg = registry(Path(directory))

        class Gate:
            inner = reg

        seed_todos(Gate(), (Todo(id="1", subject="carried over", status=TodoStatus.PENDING),))
        as_a_tool_sees_it = copy_context(reg.ctx, on_output=lambda _chunk: None)

        assert [todo.subject for todo in as_a_tool_sees_it.todos] == ["carried over"]


@pytest.mark.parametrize(
    "registry_like",
    [object(), type("NoCtx", (), {"inner": object()})()],
    ids=["no inner", "inner without a context"],
)
def test_seeding_nowhere_says_so_rather_than_raising(registry_like: object) -> None:
    # Same contract as `live_todos`: a plan is not worth an exception on a registry
    # that was assembled without a context.
    assert seed_todos(registry_like, (Todo(id="1", subject="x", status=TodoStatus.PENDING),)) is (
        False
    )
