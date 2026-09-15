"""A runnable, fully offline tour of the interface layer.

    PYTHONPATH=src uv run python -m ronin.ui.demo

No model, no network, no terminal required and **no Textual required**: the event
stream is a scripted list, exactly as it is in ``tests/ui``. That substitution is
the whole architectural point — the TUI, the headless runner and the SDK are all
just consumers of ``AsyncIterator[Event]``, so a fake list drives every one of
them.

What it proves, in order:

1. the reducer folds a stream with a ``StreamReset`` **without** duplicating the
   answer, and is renderable after every single delta;
2. tool calls collapse to one line each, including a tool nobody has heard of;
3. edits render as a real unified diff with colour markers, before approval;
4. todos render as a checklist and the status line as one line;
5. an approval block shows exactly ``ApprovalRequest.rendered``;
6. the same stream through the headless renderer emits stream-json and an exit
   code of 2, because an unattended run **denies** approval;
7. slash commands parse, including one loaded from a temp ``.ronin/commands``.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

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

from .app import TEXTUAL_MISSING, Session, initial_state, panels_for, textual_available
from .commands import ParseError, load_registry, parse, render_help
from .headless import OutputFormat, run_headless
from .reduce import (
    ToolLine,
    ViewState,
    advance_activity,
    decision_for,
    reduce_event,
    reduce_stream,
)
from .render import (
    ANSI,
    render_activity,
    render_approval,
    render_diff,
    render_status_for,
    render_todos,
    render_tool_lines,
    render_transcript,
)

RULE = "─" * 78

OLD_FILE = "def greet(name):\n    print('hi ' + name)\n\treturn None\n"
NEW_FILE = "def greet(name: str) -> None:\n    print(f'hi {name}')\n\treturn None\n"

TODOS: tuple[Todo, ...] = (
    Todo(id="1", subject="read the failing test", status=TodoStatus.COMPLETED),
    Todo(id="2", subject="fix the reducer", status=TodoStatus.IN_PROGRESS),
    Todo(id="3", subject="run the suite", status=TodoStatus.PENDING),
)

FINAL_STATE = AgentState(
    todos=TODOS,
    cwd="/home/user/Ronin",
    budget=Budget(spent_tokens=3120, spent_usd=0.0184),
    mode=Mode.ASK,
)


def script() -> tuple[Event, ...]:
    """One turn: a dropped stream, three tools, a gated edit, a clean end."""
    return (
        TurnStart(turn_index=0),
        TextDelta(text="I will read the file", thinking=True),
        TextDelta(text="Let me look at "),
        TextDelta(text="the file — DROPPED MID-SENTENCE"),
        StreamReset(reason="provider retry after a mid-stream drop"),
        TextDelta(text="Reading src/main.py first.", thinking=True),
        TextDelta(text="Looking at src/main.py "),
        TextDelta(text="and then editing it.\n"),
        ToolStart(tool_use_id="t1", name="Read", arguments={"path": "src/main.py"}),
        ToolEnd(
            tool_use_id="t1",
            name="Read",
            result=ToolResult(ok=True, content="line\n" * 240),
        ),
        ToolStart(
            tool_use_id="t2",
            name="mcp__linear__list_issues",
            arguments={"team": "core", "limit": 5},
        ),
        ToolEnd(
            tool_use_id="t2",
            name="mcp__linear__list_issues",
            result=ToolResult(ok=True, content="RON-1 fix the reducer"),
        ),
        ToolStart(tool_use_id="t3", name="Grep", arguments={"pattern": "def greet"}),
        ToolEnd(
            tool_use_id="t3",
            name="Grep",
            result=ToolResult(ok=False, error="no matches; widen the pattern or check the path"),
        ),
        ToolStart(tool_use_id="t4", name="Edit", arguments={"path": "src/main.py"}),
        ApprovalRequest(
            tool_use_id="t4",
            name="Edit",
            danger_level=DangerLevel.MUTATING,
            rendered=render_diff(OLD_FILE, NEW_FILE, path="src/main.py"),
            reason="writes to a tracked file",
        ),
        ToolEnd(
            tool_use_id="t4",
            name="Edit",
            result=ToolResult(ok=False, error="DENIED: no human attached to approve this action"),
        ),
        TurnEnd(
            turn_index=0,
            state=TurnState.DONE,
            stop_reason="no_tool_calls",
            agent_state=FINAL_STATE,
        ),
    )


def error_script() -> tuple[Event, ...]:
    """A turn that fails. Exists so the demo can show exit code 1, not claim it."""
    return (
        TurnStart(turn_index=0),
        TextDelta(text="starting"),
        Error(message="provider returned 500 twice", kind="provider", recoverable=False),
        TurnEnd(turn_index=0, state=TurnState.ERROR, stop_reason="protocol_error"),
    )


def clean_script() -> tuple[Event, ...]:
    """A turn that just answers. Exit code 0."""
    return (
        TurnStart(turn_index=0),
        TextDelta(text="two plus two is four.\n"),
        TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls"),
    )


async def stream(events: Sequence[Event]) -> AsyncIterator[Event]:
    """The scripted stand-in for the loop. Yields with a real await between events."""
    for event in events:
        await asyncio.sleep(0)
        yield event


def section(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


async def main() -> int:
    events = script()

    section("1. the reducer never buffers, and StreamReset drops only its own span")
    partials: list[str] = []
    state = ViewState().with_status(
        model="claude-sonnet", cwd="/home/user/Ronin", branch="ui-layer", context_used=0.37
    )
    async for event in stream(events):
        state = reduce_event(state, event)
        if isinstance(event, TextDelta) and not event.thinking:
            partials.append(state.text)
    print("a render was available after every delta; the first four were:")
    for partial in partials[:4]:
        print(f"  {partial!r}")
    print(f"\nstream resets seen: {state.resets}")
    print(f"'DROPPED MID-SENTENCE' still in the transcript: {'DROPPED' in state.text}")
    print(f"reasoning kept separately ({len(state.thinking)} chars), hidden by default")

    section("2. the transcript")
    print(render_transcript(state, styles=ANSI))

    section("3. tool lines — one line each, including an unknown MCP tool")
    print(render_tool_lines(state.tool_lines, styles=ANSI))

    section("4. a real unified diff, with colour markers, before approval")
    print(render_diff(OLD_FILE, NEW_FILE, path="src/main.py", styles=ANSI))
    print("\nsame file, CRLF→LF only (the ␍ marker is why this is not invisible):")
    print(render_diff("a\r\nb\r\n", "a\nb\n", path="crlf.txt", styles=ANSI))

    section("5. todos and the status line")
    print(render_todos(state.todos, styles=ANSI))
    print()
    print(render_status_for(state, styles=ANSI))

    section("5b. the activity line — what the screen shows while work is in flight")
    live = ViewState(turn_state=TurnState.THINKING)
    print("a turn that has started and produced nothing yet, as the seconds pass:")
    for elapsed in (0.4, 2.0, 9.0, 41.0):
        frame = advance_activity(live, now=elapsed, last_event_at=0.0)
        print(f"  t+{elapsed:>4}s  {render_activity(frame, styles=ANSI)}")
    print("\nthe clock appears only once the wait is worth reporting; before that the")
    print("spinner alone says 'working'. twelve concurrent reads name themselves:")
    reads = ViewState(
        turn_state=TurnState.THINKING,
        tool_lines=tuple(
            ToolLine(tool_use_id=f"c{index}", name="read_file", arguments_summary=f"f{index}.py")
            for index in range(12)
        ),
    )
    print(f"  {render_activity(advance_activity(reads, now=3.0, last_event_at=0.0), styles=ANSI)}")
    print("\nand a long command streams its tail under its own line:")
    running = ViewState(
        turn_state=TurnState.THINKING,
        tool_lines=(ToolLine(tool_use_id="t1", name="run_command", arguments_summary="pytest -q"),),
    )
    running = running.with_tool_output("t1", "collected 412 items\n....................\n")
    running = running.with_tool_output("t1", "tests/ui/test_ui_render.py .......")
    print(render_tool_lines(running.tool_lines, styles=ANSI))
    settled = ViewState(turn_state=TurnState.DONE)
    print(f"\na finished turn leaves nothing behind: {render_activity(settled, styles=ANSI)!r}")

    section("6. the approval block renders ApprovalRequest.rendered, verbatim")
    request = next(event for event in events if isinstance(event, ApprovalRequest))
    block = render_approval(request, styles=ANSI)
    print(block)
    print(f"\nrendered field reproduced byte-for-byte: {request.rendered in block}")

    section("7. the same stream, headless: stream-json + exit code")
    lines: list[str] = []
    notices: list[str] = []
    result = await run_headless(
        stream(events),
        output_format=OutputFormat.STREAM_JSON,
        write=lines.append,
        write_error=notices.append,
        flush=lambda: None,
    )
    for line in lines[:4]:
        print(line, end="")
    print(f"… {len(lines) - 5} more event lines …")
    print(lines[-1], end="")
    print(f"\nexit code: {result.exit_code}  (2 = an approval was needed and DENIED)")
    for notice in notices:
        print(f"stderr: {notice}", end="")

    for name, script_events in (("errored", error_script()), ("clean", clean_script())):
        sink: list[str] = []
        other = await run_headless(
            stream(script_events),
            output_format=OutputFormat.TEXT,
            write=sink.append,
            write_error=lambda _text: None,
        )
        print(
            f"{name} stream, --output-format=text → exit {other.exit_code}, "
            f"stdout {''.join(sink)!r}"
        )

    section("8. slash commands, including one from .ronin/commands")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".ronin" / "commands").mkdir(parents=True)
        (root / ".ronin" / "commands" / "review.md").write_text(
            "# review a file\nReview $1 for $2 issues. Full request: $ARGUMENTS "
            "(a literal $$ stays a dollar).\n",
            encoding="utf-8",
        )
        registry = load_registry(root)
        print(render_help(registry))
        print()
        for line in (
            "/cost",
            "/clear now",
            "/moddel opus",
            "/review src/main.py typing",
            "/review src/main.py",
        ):
            outcome = parse(line, registry=registry)
            if isinstance(outcome, ParseError):
                print(f"{line:<28} ✗ {outcome.display}")
            else:
                summary = outcome.body.strip() or f"builtin, argument={outcome.argument!r}"
                print(f"{line:<28} ✓ {summary}")
        print()
        print("the two outcomes are different things to the session: a builtin *acts*, and")
        print("a user command is a *prompt* — the body above is what the model is asked, so")
        print("it runs as an ordinary turn with the same budget and verification.")
        print("all thirteen builtins are wired; the dispatcher reads this registry, so a")
        print("file dropped into .ronin/commands is invokable without restarting anything.")

    section("9. answering an approval — which keys grant what, and which grant nothing")
    print("the request above is answered by a keystroke, and the mapping is a pure")
    print("function so this table is the whole behaviour, terminal or not:\n")
    for key in ("y", "a", "n", "escape", "enter", "space", "j"):
        decision = decision_for(key)
        if decision is None:
            print(f"  {key:<8} — not an answer; the request stays open")
        elif decision.approved:
            scope = "and remembered for this session" if decision.remember else "once"
            print(f"  {key:<8} → APPROVED {scope}")
        else:
            # A denial carries no words of its own — the policy engine composes them,
            # so that the human's reason (when they gave one) and the "no reason given"
            # wording have a single author. Printing `decision.reason` here would show
            # an empty pair of brackets.
            print(f"  {key:<8} → denied; the engine words it for the model")
    print("\n`enter` and `space` are deliberately absent: a held-down key or a stray")
    print("newline arriving while an approval is on screen must not be able to approve.")
    print("\nthe same keys reach the policy engine through cli.approve, which turns a")
    print("decision into its Answer — and answers 'no' whenever no human is attached,")
    print("so a script, a subagent or a dead front end refuses rather than waits.")

    section("10. the TUI is a thin skin over the same functions")
    panels = panels_for(state)
    print(f"transcript region: {len(panels.transcript)} chars of markup")
    print(f"status region:     {panels.status}")
    session = Session(events=stream(events), model="claude-sonnet", cwd=".", branch="ui-layer")
    print(f"initial state mode: {initial_state(session).mode.value}")
    await reduce_stream(session.events)  # drain, so the scripted iterator is closed
    print(f"textual installed here: {textual_available()}")
    if textual_available():
        print("`run_app(session)` would open the interactive TUI; this demo stays")
        print("non-interactive on purpose, and tests/ui drives the real app headless.")
    else:
        print(f"so `run_app` would say: {TEXTUAL_MISSING}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
