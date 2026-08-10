"""One scripted stream through the whole interface layer, wired for real.

Fakes appear at exactly one seam — the event stream, which in production is
``ronin.core.loop.run_turn``. Everything downstream is the shipped objects:
reducer, renderers, headless runner, command registry, and the app's own adapter
functions.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from ui_harness import APPROVAL, TODOS, approval_turn, stream

from ronin.core.types import (
    AgentState,
    ApprovalRequest,
    Budget,
    DangerLevel,
    Event,
    Mode,
    StreamReset,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.ui.app import Session, initial_state, panels_for
from ronin.ui.commands import ParsedCommand, load_registry, parse
from ronin.ui.headless import EXIT_NEEDS_APPROVAL, OutputFormat, run_headless
from ronin.ui.reduce import ViewState, reduce_event, reduce_stream
from ronin.ui.render import render_diff

OLD = "def f(x):\n    return x\n"
NEW = "def f(x: int) -> int:\n    return x + 1\n"


def edit_turn() -> tuple[Event, ...]:
    """A turn that reads, retries its stream, then asks to edit a file."""
    diff = render_diff(OLD, NEW, path="src/f.py")
    return (
        TurnStart(turn_index=0),
        TextDelta(text="Thinking about types.", thinking=True),
        TextDelta(text="I will read src/f.py. THIS WAS DROPPED"),
        StreamReset(reason="failover to the backup provider"),
        TextDelta(text="Reading src/f.py, "),
        ToolStart(tool_use_id="r1", name="Read", arguments={"path": "src/f.py"}),
        ToolEnd(tool_use_id="r1", name="Read", result=ToolResult(ok=True, content=OLD)),
        TextDelta(text="then editing it."),
        ToolStart(tool_use_id="e1", name="Edit", arguments={"path": "src/f.py"}),
        ApprovalRequest(
            tool_use_id="e1",
            name="Edit",
            danger_level=DangerLevel.MUTATING,
            rendered=diff,
            reason="writes a tracked file",
        ),
        ToolEnd(
            tool_use_id="e1",
            name="Edit",
            result=ToolResult(ok=False, error="DENIED: no human attached to approve this action"),
        ),
        TurnEnd(
            turn_index=0,
            state=TurnState.DONE,
            stop_reason="no_tool_calls",
            agent_state=AgentState(
                todos=TODOS, cwd="/repo", budget=Budget(spent_tokens=900, spent_usd=0.01)
            ),
        ),
    )


async def test_one_stream_drives_the_tui_and_the_headless_runner_identically() -> None:
    # Two independent replays of the same script, one painting panels and one
    # emitting JSON lines: the answer text must be identical, because they are the
    # same fold.
    tui_state = initial_state(
        Session(events=stream(()), model="claude-sonnet", cwd="/repo", branch="main")
    )
    paints: list[str] = []
    async for event in stream(edit_turn()):
        tui_state = reduce_event(tui_state, event)
        paints.append(panels_for(tui_state).transcript)

    out: list[str] = []
    result = await run_headless(
        stream(edit_turn()),
        output_format=OutputFormat.STREAM_JSON,
        write=out.append,
        write_error=lambda _text: None,
        flush=lambda: None,
    )

    assert tui_state.text == result.text == "Reading src/f.py, then editing it."
    assert "THIS WAS DROPPED" not in tui_state.text
    assert result.exit_code == EXIT_NEEDS_APPROVAL
    # a paint per event, and the transcript only ever grows or is reset
    assert len(paints) == len(edit_turn())


async def test_the_diff_the_human_sees_is_the_diff_the_stream_carried() -> None:
    state = await reduce_stream(stream(edit_turn()[:10]))
    assert state.pending_approval is not None
    panels = panels_for(state)
    assert state.pending_approval.rendered in panels.approval
    assert "-def f(x):" in panels.approval
    assert "+def f(x: int) -> int:" in panels.approval


async def test_the_headless_stream_json_is_parseable_line_by_line() -> None:
    out: list[str] = []
    await run_headless(
        stream(edit_turn()),
        output_format=OutputFormat.STREAM_JSON,
        write=out.append,
        write_error=lambda _text: None,
        flush=lambda: None,
    )
    records = [json.loads(line) for line in out]
    assert records[0]["type"] == "turn_start"
    assert records[-1]["type"] == "result"
    approval = next(record for record in records if record["type"] == "approval_request")
    assert approval["rendered"].startswith("--- a/src/f.py")


async def test_a_consumer_can_render_before_the_stream_finishes() -> None:
    # A slow producer: the assertion is that a full render exists while the
    # generator is still suspended, i.e. nothing is buffered to TurnEnd.
    async def slow() -> AsyncIterator[Event]:
        yield TurnStart(turn_index=0)
        yield TextDelta(text="partial answer")
        await asyncio.sleep(0)
        yield TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls")

    seen: list[str] = []
    state = ViewState()
    async for event in slow():
        state = reduce_event(state, event)
        seen.append(panels_for(state).transcript)
    assert "partial answer" in seen[1]
    assert seen[1] == seen[2]


async def test_a_slash_command_and_a_turn_share_one_ui_state(tmp_path: Path) -> None:
    directory = tmp_path / ".ronin" / "commands"
    directory.mkdir(parents=True)
    (directory / "tighten.md").write_text("Tighten the types in $1.\n", encoding="utf-8")
    registry = load_registry(tmp_path)

    parsed = parse("/tighten src/f.py", registry=registry)
    assert isinstance(parsed, ParsedCommand)
    assert parsed.body == "Tighten the types in src/f.py.\n"

    state = await reduce_stream(stream(edit_turn()), state=ViewState().with_mode(Mode.PLAN))
    # the loop's own mode wins on TurnEnd, and the todos arrive with it
    assert state.mode is Mode.ASK
    assert state.todos == TODOS
    assert state.cost_usd == 0.01


async def test_an_unattended_run_denies_and_reports_rather_than_hanging() -> None:
    notices: list[str] = []
    result = await run_headless(
        stream(approval_turn()),
        output_format=OutputFormat.TEXT,
        write=lambda _text: None,
        write_error=notices.append,
        flush=lambda: None,
    )
    assert result.exit_code == EXIT_NEEDS_APPROVAL
    assert result.approvals == (APPROVAL,)
    assert any(APPROVAL.rendered in notice for notice in notices)
