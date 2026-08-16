"""The activity line: what the screen says while work is in flight.

The friction this closes is "is it working or is it stuck?" — between events the TUI
used to be a still image, so a model thinking for thirty seconds and a dead process
looked identical. These tests pin the three things that fix it: a spinner that advances
on a clock the test injects, a name-blind label for whatever is running, and the tail of
a long tool's output under its own line.

Everything here is pure except the last test, which drives the real Textual app headless
over a scripted event stream. No model, no network, and no sleeping: time is a number
passed in.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from ui_harness import stream

from ronin.core.types import (
    ApprovalRequest,
    DangerLevel,
    TextDelta,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.ui.reduce import (
    SPINNER_FRAMES,
    SPINNER_INTERVAL_SECONDS,
    THINKING_LABEL,
    TOOL_OUTPUT_TAIL_LINES,
    WAITING_LABEL,
    ToolLine,
    ViewState,
    activity_label,
    advance_activity,
    reduce_all,
    spinner_frame,
)
from ronin.ui.render import (
    ACTIVITY_ELAPSED_AFTER_SECONDS,
    PLAIN,
    render_activity,
    render_panels,
    render_tool_lines,
    render_tool_output,
)


def _live(**kwargs: object) -> ViewState:
    """A state mid-turn: started, not finished — the only time activity is shown."""
    return ViewState(turn_state=TurnState.THINKING, **kwargs)  # type: ignore[arg-type]


def _tool(name: str, *, uid: str = "t1", args: str = "", ok: bool | None = None) -> ToolLine:
    return ToolLine(tool_use_id=uid, name=name, arguments_summary=args, ok=ok)


# --------------------------------------------------------------------------- #
# busy — who is blocked on whom
# --------------------------------------------------------------------------- #


def test_a_turn_in_flight_is_busy_and_a_finished_one_is_not() -> None:
    assert _live().busy
    for done in (TurnState.DONE, TurnState.ERROR, TurnState.INTERRUPTED):
        assert not ViewState(turn_state=done).busy


def test_a_session_that_has_not_started_is_not_busy() -> None:
    assert not ViewState().busy  # no turn yet — an idle prompt is not "working"


def test_a_turn_waiting_on_an_approval_is_not_busy() -> None:
    # The human is the one being waited on. Animating a spinner at someone who is being
    # asked a question tells them the wrong thing about who is blocked.
    parked = _live(
        pending_approval=ApprovalRequest(
            tool_use_id="t9", name="Bash", danger_level=DangerLevel.DESTRUCTIVE, rendered="rm -rf ."
        )
    )
    assert not parked.busy
    assert render_activity(parked) == ""


# --------------------------------------------------------------------------- #
# the label — derived from shape, never from a table of tool names
# --------------------------------------------------------------------------- #


def test_a_live_turn_with_nothing_yet_says_it_is_waiting() -> None:
    assert activity_label(_live()) == WAITING_LABEL


def test_streaming_reasoning_is_labelled_without_showing_the_reasoning() -> None:
    state = _live(span_thinking="the user probably means the second file, because")
    assert activity_label(state) == THINKING_LABEL
    # The label, never the content: `show_thinking` decides whether reasoning is shown,
    # and a progress indicator must not leak it just because it needed something to say.
    assert "second file" not in render_activity(state)


def test_streaming_prose_needs_no_label_because_the_prose_is_the_progress() -> None:
    assert activity_label(_live(span_text="Reading the file now")) == ""


def test_one_running_tool_names_itself_with_its_argument() -> None:
    state = _live(tool_lines=(_tool("run_command", args="pytest -q"),))
    assert activity_label(state) == "run_command(pytest -q)"


def test_a_tool_with_no_arguments_renders_bare() -> None:
    assert activity_label(_live(tool_lines=(_tool("repo_map"),))) == "repo_map"


def test_twelve_reads_read_as_twelve_reads_with_no_name_table() -> None:
    # "reading 12 files", derived: the tool names itself and the reducer only counts.
    lines = tuple(_tool("read_file", uid=f"c{i}", args=f"f{i}.py") for i in range(12))
    assert activity_label(_live(tool_lines=lines)) == "read_file x12"


def test_a_mixed_batch_lists_distinct_names_and_the_count() -> None:
    lines = (_tool("read_file", uid="a"), _tool("grep", uid="b"), _tool("read_file", uid="c"))
    label = activity_label(_live(tool_lines=lines))
    assert label == "grep, read_file x3"


def test_a_finished_tool_stops_being_the_activity() -> None:
    # ok is set, so the line is no longer running and cannot be what is happening now.
    state = _live(tool_lines=(_tool("read_file", ok=True),))
    assert activity_label(state) == WAITING_LABEL


def test_an_unknown_mcp_tool_gets_the_same_treatment_as_a_builtin() -> None:
    state = _live(tool_lines=(_tool("acme__frobnicate", args="widget-7"),))
    assert activity_label(state) == "acme__frobnicate(widget-7)"


# --------------------------------------------------------------------------- #
# the clock and the spinner
# --------------------------------------------------------------------------- #


def test_the_spinner_frame_is_a_pure_function_of_the_tick() -> None:
    assert spinner_frame(0) == SPINNER_FRAMES[0]
    assert spinner_frame(len(SPINNER_FRAMES)) == SPINNER_FRAMES[0]  # wraps
    assert spinner_frame(-1) == SPINNER_FRAMES[-1]  # a backwards clock still renders


def test_every_frame_is_one_column_so_the_line_does_not_jump() -> None:
    assert len({len(frame) for frame in SPINNER_FRAMES}) == 1


def test_advance_derives_the_tick_from_elapsed_time_not_a_counter() -> None:
    # Derived, so a dropped or late timer callback shows the frame the clock justifies
    # rather than falling permanently behind.
    state = advance_activity(_live(), now=SPINNER_INTERVAL_SECONDS * 7, last_event_at=0.0)
    assert state.tick == 7
    assert state.waiting_seconds == pytest.approx(SPINNER_INTERVAL_SECONDS * 7)


def test_a_backwards_clock_yields_no_wait_rather_than_a_negative_one() -> None:
    state = advance_activity(_live(), now=5.0, last_event_at=9.0)
    assert state.waiting_seconds == 0.0
    assert state.tick == 0


def test_with_activity_refuses_a_negative_wait() -> None:
    with pytest.raises(ValueError, match="waiting_seconds"):
        ViewState().with_activity(tick=0, waiting_seconds=-1.0)


def test_a_quick_tool_shows_the_spinner_but_no_clock() -> None:
    fast = advance_activity(_live(), now=ACTIVITY_ELAPSED_AFTER_SECONDS / 2, last_event_at=0.0)
    rendered = render_activity(fast)
    assert WAITING_LABEL in rendered
    assert "s" not in rendered.replace(WAITING_LABEL, "")  # no elapsed figure yet


def test_the_clock_appears_once_the_wait_is_worth_reporting() -> None:
    slow = advance_activity(_live(), now=9.4, last_event_at=0.0)
    assert "9s" in render_activity(slow)


def test_the_clock_keeps_counting_on_a_long_silence() -> None:
    assert "41s" in render_activity(advance_activity(_live(), now=41.9, last_event_at=0.0))


def test_a_settled_screen_carries_no_residue_of_the_last_turn() -> None:
    done = advance_activity(ViewState(turn_state=TurnState.DONE), now=99.0, last_event_at=0.0)
    assert render_activity(done) == ""


# --------------------------------------------------------------------------- #
# streamed tool output — the expansion under a running line
# --------------------------------------------------------------------------- #


def test_output_arrives_under_the_running_tool_that_produced_it() -> None:
    state = _live(tool_lines=(_tool("run_command", args="pytest -q"),))
    state = state.with_tool_output("t1", "collected 412 items\n")
    assert state.tool_lines[0].output_tail[0] == "collected 412 items"
    assert "collected 412 items" in render_tool_lines(state.tool_lines)


def test_a_chunk_that_starts_mid_line_continues_that_line() -> None:
    # Pipes split wherever they like; a chunk boundary must not become a line break.
    state = _live(tool_lines=(_tool("run_command"),))
    state = state.with_tool_output("t1", "tests/ui/test_")
    state = state.with_tool_output("t1", "render.py ....")
    assert state.tool_lines[0].output_tail == ("tests/ui/test_render.py ....",)


def test_only_the_tail_is_kept_so_a_dev_server_cannot_grow_the_pane() -> None:
    state = _live(tool_lines=(_tool("run_command"),))
    state = state.with_tool_output("t1", "".join(f"line {n}\n" for n in range(200)))
    tail = state.tool_lines[0].output_tail
    assert len(tail) <= TOOL_OUTPUT_TAIL_LINES
    assert "line 199" in tail[-1] or "line 199" in tail[-2]  # the newest survives
    assert not any(item == "line 0" for item in tail)  # the oldest does not


def test_carriage_returns_do_not_smuggle_a_second_line_in() -> None:
    state = _live(tool_lines=(_tool("run_command"),))
    state = state.with_tool_output("t1", "step one\r\nstep two\r\n")
    assert state.tool_lines[0].output_tail[:2] == ("step one", "step two")


def test_a_trailing_newline_does_not_render_a_blank_row() -> None:
    state = _live(tool_lines=(_tool("run_command"),))
    state = state.with_tool_output("t1", "done\n")
    assert render_tool_output(state.tool_lines[0], styles=PLAIN).splitlines() == ["  │ done"]


def test_output_for_an_unknown_or_finished_tool_is_dropped_not_raised() -> None:
    # Output racing a ToolEnd is normal; raising inside a paint path would not be.
    state = _live(tool_lines=(_tool("run_command", ok=True),))
    assert state.with_tool_output("t1", "late") is state
    assert state.with_tool_output("nope", "late") is state


def test_an_empty_chunk_changes_nothing() -> None:
    state = _live(tool_lines=(_tool("run_command"),))
    assert state.with_tool_output("t1", "") is state


def test_a_tool_that_streams_nothing_renders_exactly_as_before() -> None:
    quiet = _tool("read_file", args="a.py")
    assert render_tool_output(quiet) == ""
    assert render_tool_lines((quiet,)) == render_tool_lines((quiet,))
    assert "│" not in render_tool_lines((quiet,))


# --------------------------------------------------------------------------- #
# the panel, and the fold it sits on
# --------------------------------------------------------------------------- #


def test_the_activity_panel_is_rendered_from_the_same_state_as_everything_else() -> None:
    state = advance_activity(
        _live(tool_lines=(_tool("run_command", args="pytest -q"),)), now=4.0, last_event_at=0.0
    )
    panels = render_panels(state, styles=PLAIN)
    assert "run_command(pytest -q)" in panels.activity
    assert "4s" in panels.activity


def test_a_scripted_stream_folds_into_a_busy_state_then_a_settled_one() -> None:
    mid = reduce_all(
        (
            TurnStart(turn_index=0),
            TextDelta(text="looking"),
            ToolStart(tool_use_id="t1", name="run_command", arguments={"command": "pytest -q"}),
        )
    )
    assert mid.busy and activity_label(mid) == "run_command(pytest -q)"
    settled = reduce_all(
        (TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls"),), mid
    )
    assert not settled.busy and render_activity(settled) == ""


# --------------------------------------------------------------------------- #
# the real app, driven headless over a scripted stream
# --------------------------------------------------------------------------- #

pytest.importorskip("textual", reason="the interactive TUI needs the 'tui' extra")


async def test_the_app_shows_the_activity_line_while_a_tool_runs_and_clears_it_after() -> None:
    """The whole point, end to end: a paused stream, a real app, a fake clock."""
    from textual.widgets import Static

    from ronin.ui.app import ACTIVITY_ID, Session, _build_app

    # A turn that starts a tool and never ends it — the screen a hung command produces.
    hanging = (
        TurnStart(turn_index=0),
        ToolStart(tool_use_id="t1", name="run_command", arguments={"command": "pytest -q"}),
    )
    now = [0.0]
    app = _build_app(Session(events=stream(hanging), clock=lambda: now[0]))
    async with app.run_test() as pilot:
        await pilot.pause()
        now[0] = 12.0  # twelve seconds of silence, without waiting twelve seconds
        app._tick()  # the interval callback the app schedules on mount
        await pilot.pause()
        shown = str(app.query_one(f"#{ACTIVITY_ID}", Static).visual)
        assert "run_command(pytest -q)" in shown
        assert "12s" in shown

        # The turn ends: the line must clear rather than leave a stale spinner behind.
        app.state = replace(app.state, turn_state=TurnState.DONE)
        app._paint()
        await pilot.pause()
        assert str(app.query_one(f"#{ACTIVITY_ID}", Static).visual) == ""


async def test_the_ticker_does_not_repaint_an_idle_session() -> None:
    """A settled screen must not be redrawn ten times a second for an empty line."""
    from ronin.ui.app import Session, _build_app

    settled = (TurnStart(turn_index=0), TurnEnd(turn_index=0, state=TurnState.DONE))
    app = _build_app(Session(events=stream(settled), clock=lambda: 0.0))
    async with app.run_test() as pilot:
        await pilot.pause()
        before = app.state
        app._tick()
        assert app.state is before  # not busy → the tick is a no-op, not a repaint
