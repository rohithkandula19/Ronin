"""Scripted event streams for the interface-layer tests. No model, no network.

Named ``ui_harness`` rather than ``harness``/``fakes``: the test trees are not
packages, so every module is importable by its bare basename and two directories
cannot share one. A generic name here would shadow another subsystem's helper and
break its suite.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from ronin.core.types import (
    AgentState,
    ApprovalRequest,
    Budget,
    DangerLevel,
    Error,
    Event,
    Mode,
    StreamReset,
    TextDelta,
    Todo,
    TodoStatus,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)

TODOS: tuple[Todo, ...] = (
    Todo(id="1", subject="read the test", status=TodoStatus.COMPLETED),
    Todo(id="2", subject="fix the bug", status=TodoStatus.IN_PROGRESS),
    Todo(id="3", subject="run the suite", status=TodoStatus.PENDING),
)

END_STATE = AgentState(
    todos=TODOS,
    cwd="/repo",
    budget=Budget(spent_tokens=1234, spent_usd=0.0456),
    mode=Mode.ASK,
)

APPROVAL = ApprovalRequest(
    tool_use_id="t9",
    name="Bash",
    danger_level=DangerLevel.DESTRUCTIVE,
    rendered="rm -rf ./build",
    reason="destructive",
)


def happy_turn() -> tuple[Event, ...]:
    """A turn with a dropped stream, a known tool, an unknown tool, and a clean end."""
    return (
        TurnStart(turn_index=0),
        TextDelta(text="thinking out loud", thinking=True),
        TextDelta(text="I will read "),
        TextDelta(text="the file. DUPLICATE"),
        StreamReset(reason="failover"),
        TextDelta(text="Reading "),
        TextDelta(text="src/main.py now."),
        ToolStart(tool_use_id="t1", name="Read", arguments={"path": "src/main.py"}),
        ToolEnd(
            tool_use_id="t1", name="Read", result=ToolResult(ok=True, content="x\n" * 240)
        ),
        ToolStart(
            tool_use_id="t2",
            name="mcp__unheard_of__do_thing",
            arguments={"widget": 7, "colour": "red"},
        ),
        ToolEnd(
            tool_use_id="t2",
            name="mcp__unheard_of__do_thing",
            result=ToolResult(ok=True, content="done"),
        ),
        TurnEnd(
            turn_index=0,
            state=TurnState.DONE,
            stop_reason="no_tool_calls",
            agent_state=END_STATE,
        ),
    )


def approval_turn() -> tuple[Event, ...]:
    """A turn that stops on a gated tool and gets denied."""
    return (
        TurnStart(turn_index=0),
        TextDelta(text="I need to delete the build directory."),
        ToolStart(tool_use_id="t9", name="Bash", arguments={"command": "rm -rf ./build"}),
        APPROVAL,
        ToolEnd(
            tool_use_id="t9",
            name="Bash",
            result=ToolResult(ok=False, error="DENIED: no human attached to approve this action"),
        ),
        TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls"),
    )


def errored_turn() -> tuple[Event, ...]:
    return (
        TurnStart(turn_index=0),
        TextDelta(text="partial"),
        Error(message="provider returned 500 twice", kind="provider", recoverable=False),
        TurnEnd(turn_index=0, state=TurnState.ERROR, stop_reason="protocol_error"),
    )


def clean_turn() -> tuple[Event, ...]:
    return (
        TurnStart(turn_index=0),
        TextDelta(text="four."),
        TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls"),
    )


def interrupted_turn() -> tuple[Event, ...]:
    return (
        TurnStart(turn_index=0),
        TextDelta(text="half an ans"),
        TurnEnd(turn_index=0, state=TurnState.INTERRUPTED, stop_reason="interrupted"),
    )


def every_event() -> tuple[Event, ...]:
    """One instance of every member of the ``Event`` union, for schema coverage."""
    return (
        TurnStart(turn_index=0),
        TextDelta(text="hi", thinking=False),
        StreamReset(reason="retry"),
        ToolStart(tool_use_id="t1", name="Read", arguments={"path": "a.py"}),
        ToolEnd(
            tool_use_id="t1",
            name="Read",
            result=ToolResult(ok=True, content="x", artifacts=("a.png",), tokens_estimate=3),
        ),
        APPROVAL,
        Error(message="boom", kind="provider", recoverable=True),
        TurnEnd(
            turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls", agent_state=END_STATE
        ),
    )


async def stream(events: Sequence[Event]) -> AsyncIterator[Event]:
    """Replay a scripted list as the ``AsyncIterator[Event]`` the loop would hand over."""
    for event in events:
        yield event
