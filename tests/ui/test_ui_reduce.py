"""The reducer: the module the whole interface layer rests on."""

from __future__ import annotations

import pytest
from ui_harness import APPROVAL, END_STATE, TODOS, approval_turn, happy_turn, stream

from ronin.core.types import (
    AgentState,
    Budget,
    Compaction,
    DangerLevel,
    Error,
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
    VerifyResult,
)
from ronin.ui.reduce import (
    ViewState,
    reduce_all,
    reduce_event,
    reduce_stream,
    summarize_arguments,
    summarize_result,
)


def test_a_stream_reset_drops_only_the_current_span() -> None:
    state = reduce_all(happy_turn())
    assert "DUPLICATE" not in state.text
    assert state.text == "Reading src/main.py now."
    assert state.resets == 1


def test_a_stream_reset_keeps_text_from_earlier_turn_indices() -> None:
    # The regression the provider layer also guards: a reset in turn 1 must not
    # erase what turn 0 already said.
    events = (
        TurnStart(turn_index=0),
        TextDelta(text="first turn. "),
        TurnStart(turn_index=1),
        TextDelta(text="second turn, DROPPED"),
        StreamReset(reason="failover"),
        TextDelta(text="second turn, retried."),
    )
    state = reduce_all(events)
    assert state.text == "first turn. second turn, retried."


def test_two_resets_in_a_row_leave_the_committed_prefix_intact() -> None:
    events = (
        TurnStart(turn_index=0),
        TextDelta(text="keep. "),
        TurnStart(turn_index=1),
        TextDelta(text="a"),
        StreamReset(),
        TextDelta(text="b"),
        StreamReset(),
        TextDelta(text="c"),
    )
    assert reduce_all(events).text == "keep. c"


def test_a_reset_discards_the_reasoning_of_the_same_span() -> None:
    events = (
        TurnStart(turn_index=0),
        TextDelta(text="reasoning that was retried", thinking=True),
        StreamReset(),
        TextDelta(text="the real reasoning", thinking=True),
    )
    state = reduce_all(events)
    assert state.thinking == "the real reasoning"
    assert state.text == ""


def test_thinking_is_tracked_apart_from_the_answer() -> None:
    state = reduce_all((TextDelta(text="reason", thinking=True), TextDelta(text="answer")))
    assert state.thinking == "reason"
    assert state.text == "answer"


async def test_a_partial_render_is_available_after_every_delta() -> None:
    seen: list[str] = []

    def record(state: ViewState, _event: object) -> None:
        seen.append(state.text)

    final = await reduce_stream(stream(happy_turn()), on_state=record)
    # One observation per event, and the answer grows one delta at a time — no
    # renderer waits for TurnEnd.
    assert len(seen) == len(happy_turn())
    assert "Reading " in seen
    assert seen[-1] == final.text


def test_a_tool_line_collapses_to_one_line() -> None:
    state = reduce_all(happy_turn())
    read = state.tool_lines[0]
    assert read.text == "● Read(src/main.py) → 240 lines"


def test_an_unknown_tool_renders_from_the_shape_of_its_arguments() -> None:
    state = reduce_all(happy_turn())
    unknown = state.tool_lines[1]
    assert unknown.text == "● mcp__unheard_of__do_thing(colour=red, widget=7) → done"


def test_a_running_tool_is_marked_running_not_successful() -> None:
    state = reduce_all(
        (ToolStart(tool_use_id="t1", name="Bash", arguments={"command": "sleep 1"}),)
    )
    line = state.tool_lines[0]
    assert line.running is True
    assert line.ok is None
    assert line.text == "● Bash(sleep 1) …"
    assert state.running_tools == (line,)


def test_a_tool_end_with_no_start_is_shown_rather_than_dropped() -> None:
    state = reduce_all(
        (ToolEnd(tool_use_id="ghost", name="Read", result=ToolResult(ok=True, content="x")),)
    )
    assert len(state.tool_lines) == 1
    assert state.tool_lines[0].ok is True


def test_two_calls_of_the_same_tool_close_in_order() -> None:
    events = (
        ToolStart(tool_use_id="a", name="Read", arguments={"path": "one"}),
        ToolStart(tool_use_id="b", name="Read", arguments={"path": "two"}),
        ToolEnd(tool_use_id="b", name="Read", result=ToolResult(ok=True, content="2")),
    )
    state = reduce_all(events)
    assert [line.running for line in state.tool_lines] == [True, False]
    assert state.tool_lines[1].arguments_summary == "two"


def test_a_failed_tool_keeps_the_failure_in_text_not_only_in_colour() -> None:
    events = (
        ToolStart(tool_use_id="t", name="Grep", arguments={"pattern": "x"}),
        ToolEnd(
            tool_use_id="t",
            name="Grep",
            result=ToolResult(ok=False, error="no matches; widen the pattern"),
        ),
    )
    line = reduce_all(events).tool_lines[0]
    assert line.ok is False
    assert line.text == "● Grep(x) → error: no matches; widen the pattern"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({}, ""),
        ({"path": "src/a.py"}, "src/a.py"),
        ({"command": "ls -la", "timeout": 30}, "ls -la"),
        ({"only": "value"}, "value"),
        ({"b": 2, "a": 1}, "a=1, b=2"),
        ({"path": "a\nb"}, "a ⏎ b"),
        ({"flag": True, "none": None}, "flag=True, none=None"),
        ({"nested": {"k": [1, 2]}}, '{"k": [1, 2]}'),
    ],
)
def test_argument_summaries_are_derived_generically(
    arguments: dict[str, object], expected: str
) -> None:
    assert summarize_arguments(arguments) == expected


def test_an_enormous_argument_is_clipped_with_a_marker() -> None:
    summary = summarize_arguments({"path": "x" * 500})
    assert len(summary) == 64
    assert summary.endswith("…")


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (ToolResult(ok=True, content=""), "ok"),
        (ToolResult(ok=True, content="one line"), "one line"),
        (ToolResult(ok=True, content="one line\n"), "one line"),
        (ToolResult(ok=True, content="a\nb\nc"), "3 lines"),
        (ToolResult(ok=True, content="a\nb\nc\n"), "3 lines"),
        (ToolResult(ok=False, error="nope"), "error: nope"),
    ],
)
def test_result_summaries_are_derived_from_the_tool_result_alone(
    result: ToolResult, expected: str
) -> None:
    assert summarize_result(result) == expected


def test_a_pending_approval_is_held_until_its_tool_ends() -> None:
    events = approval_turn()
    before = reduce_all(events[:4])
    assert before.pending_approval == APPROVAL
    after = reduce_all(events[:5])
    assert after.pending_approval is None


def test_a_pending_approval_is_cleared_by_turn_end() -> None:
    state = reduce_all(
        (
            APPROVAL,
            TurnEnd(turn_index=0, state=TurnState.INTERRUPTED, stop_reason="interrupted"),
        )
    )
    assert state.pending_approval is None


def test_turn_end_folds_todos_spend_mode_and_cwd_from_agent_state() -> None:
    state = reduce_all(happy_turn())
    assert state.todos == TODOS
    assert state.cost_usd == END_STATE.budget.spent_usd
    assert state.tokens == END_STATE.budget.spent_tokens
    assert state.cwd == "/repo"
    assert state.mode is Mode.ASK
    assert state.stop_reason == "no_tool_calls"
    assert state.finished is True


def test_turn_end_without_agent_state_keeps_what_the_ui_already_had() -> None:
    seeded = ViewState().with_todos(TODOS).with_status(cost_usd=1.5)
    state = reduce_event(
        seeded, TurnEnd(turn_index=3, state=TurnState.DONE, stop_reason="max_iterations")
    )
    assert state.todos == TODOS
    assert state.cost_usd == 1.5
    assert state.turn_index == 3


def test_a_live_todo_list_can_be_pushed_in_without_the_reducer_knowing_tool_names() -> None:
    mid_turn = ViewState().with_todos(
        (Todo(id="1", subject="in flight", status=TodoStatus.IN_PROGRESS),)
    )
    assert mid_turn.todos[0].status is TodoStatus.IN_PROGRESS


def test_errors_accumulate_and_do_not_end_the_state() -> None:
    state = reduce_all(
        (
            Error(message="first", kind="provider"),
            Error(message="second", kind="tool", recoverable=True),
        )
    )
    assert [error.message for error in state.errors] == ["first", "second"]
    assert state.finished is False


def test_status_injection_leaves_everything_else_alone() -> None:
    folded = reduce_all(happy_turn())
    with_status = folded.with_status(model="opus", branch="main", context_used=0.5)
    assert with_status.model == "opus"
    assert with_status.branch == "main"
    assert with_status.context_used == 0.5
    assert with_status.text == folded.text
    assert with_status.tool_lines == folded.tool_lines


def test_the_view_state_is_frozen() -> None:
    state = ViewState()
    with pytest.raises(AttributeError):
        state.span_text = "no"  # type: ignore[misc]  # frozen by design


def test_an_agent_state_with_a_wider_mode_is_reported_not_hidden() -> None:
    # The status line must show what the loop actually ran with, even when the UI
    # had a narrower mode selected.
    seeded = ViewState().with_mode(Mode.PLAN)
    end = TurnEnd(
        turn_index=0,
        state=TurnState.DONE,
        stop_reason="no_tool_calls",
        agent_state=AgentState(mode=Mode.FULL, budget=Budget()),
    )
    assert reduce_event(seeded, end).mode is Mode.FULL


def test_the_danger_level_travels_on_the_pending_approval() -> None:
    state = reduce_all((APPROVAL,))
    assert state.pending_approval is not None
    assert state.pending_approval.danger_level is DangerLevel.DESTRUCTIVE


def test_compaction_and_verify_are_ignored_rather_than_filed_as_errors() -> None:
    """`ViewState` has no field for either yet. What must NOT happen is them
    landing in `state.errors`, which is typed `tuple[Error, ...]` and would make
    the TUI report a successful verification as a failure."""
    state = reduce_all((TurnStart(turn_index=0),))
    after_compaction = reduce_event(state, Compaction(folded_messages=4))
    after_verify = reduce_event(after_compaction, VerifyResult(ran=True, passed=True))
    assert after_compaction.errors == ()
    assert after_verify.errors == ()
    assert after_verify == state
