"""Replay: the reset rule, the reconciliation, and the post-crash repair."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
from persistence_harness import (
    READ_RESULT,
    READ_USE,
    SIGNATURE,
    crashed_session,
    interrupted_tail,
    truncate_mid_line,
    two_turn_session,
)

from ronin.core.types import (
    AgentState,
    Message,
    Mode,
    Role,
    StreamReset,
    Text,
    TextDelta,
    Thinking,
    Todo,
    ToolEnd,
    ToolResult,
    ToolResultBlock,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.persistence.resume import (
    INTERRUPTED_NO_RESULT,
    StateSource,
    fold_turns,
    load_session,
    replay,
    resume,
    resume_latest,
    same_cwd,
    sessions_for_cwd,
)
from ronin.persistence.transcript import Transcript, TranscriptError, sessions_dir

# --------------------------------------------------------------------------- #
# StreamReset
# --------------------------------------------------------------------------- #


def test_a_stream_reset_discards_the_text_emitted_before_it() -> None:
    """A replay that keeps both halves shows the assistant answering twice — the
    duplication the provider layer already has regression tests for."""
    turns = fold_turns(two_turn_session())
    assert "DISCARD ME" not in turns[0].text
    assert turns[0].text == "Reading src/app.py."
    assert turns[0].thinking == "look at it"
    assert turns[0].resets == 1


def test_a_stream_reset_discards_reasoning_too() -> None:
    events = (
        TurnStart(turn_index=0),
        TextDelta(text="old reasoning", thinking=True),
        TextDelta(text="old prose"),
        StreamReset(reason="retry"),
        TextDelta(text="new prose"),
    )
    turn = fold_turns(events)[0]
    assert (turn.text, turn.thinking) == ("new prose", "")


def test_a_stream_reset_does_not_un_run_a_tool() -> None:
    """"A tool that already ran has already had its effect, and no event can undo
    that" — the reset's scope is text only."""
    events = (
        TurnStart(turn_index=0),
        ToolStart(tool_use_id="t1", name="read"),
        ToolEnd(tool_use_id="t1", name="read", result=ToolResult(ok=True, content="x")),
        TextDelta(text="prose"),
        StreamReset(reason="retry"),
    )
    turn = fold_turns(events)[0]
    assert turn.text == ""
    assert [call.id for call in turn.calls] == ["t1"]
    assert [block.tool_use_id for block in turn.results] == ["t1"]


def test_a_turn_start_also_ends_the_previous_turns_text() -> None:
    turns = fold_turns(two_turn_session())
    assert len(turns) == 2
    assert turns[1].text == "handler() returns None."
    assert turns[1].index == 1


def test_folding_an_empty_stream_yields_no_turns() -> None:
    assert fold_turns(()) == ()


def test_a_stream_that_begins_mid_turn_still_folds() -> None:
    """A transcript whose head was trimmed must not silently drop its events."""
    turns = fold_turns((TextDelta(text="orphan"), TurnEnd(turn_index=4, state=TurnState.DONE)))
    assert len(turns) == 1
    assert turns[0].text == "orphan" and turns[0].index == 4


def test_results_are_ordered_by_the_call_they_answer() -> None:
    """Providers read the batch in call order; a reordered fold changes the meaning
    of a parallel read."""
    events = (
        TurnStart(turn_index=0),
        ToolStart(tool_use_id="a", name="read"),
        ToolStart(tool_use_id="b", name="read"),
        ToolEnd(tool_use_id="b", name="read", result=ToolResult(ok=True, content="B")),
        ToolEnd(tool_use_id="a", name="read", result=ToolResult(ok=True, content="A")),
    )
    turn = fold_turns(events)[0]
    assert [block.tool_use_id for block in turn.results] == ["a", "b"]


# --------------------------------------------------------------------------- #
# Which state wins
# --------------------------------------------------------------------------- #


def test_a_recorded_agent_state_is_preferred_and_carries_what_events_cannot() -> None:
    """The recorded state is strictly richer: signatures, budget, mode, todos."""
    result = replay(two_turn_session())
    assert result.source is StateSource.RECORDED
    assert result.mismatches == ()
    assert result.repairs == ()
    assert result.clean
    signatures = [
        block.signature
        for message in result.state.messages
        for block in message.content_blocks
        if isinstance(block, Thinking)
    ]
    assert signatures == [SIGNATURE]
    assert result.state.budget.spent_usd == 0.0231
    assert result.state.cwd == "/work/repo"


def test_without_any_recorded_state_the_fold_is_used_and_says_what_it_lost() -> None:
    events = tuple(
        event for event in two_turn_session() if not isinstance(event, TurnEnd)
    )
    result = replay(events)
    assert result.source is StateSource.FOLDED
    assert any("folded from events alone" in note for note in result.notes)
    roles = [message.role for message in result.state.messages]
    assert roles == [Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    # The user's prompt is not an event, so a fold cannot recover it.
    assert Role.USER not in roles
    thinking = [
        block
        for message in result.state.messages
        for block in message.content_blocks
        if isinstance(block, Thinking)
    ]
    assert thinking and thinking[0].signature == "", "no event carries a signature"


def test_turns_after_the_last_checkpoint_are_folded_on_top_of_it() -> None:
    """A crash happens mid-turn, so the newest checkpoint is behind the file's end;
    taking it alone would throw away the turn the session died in."""
    result = replay(crashed_session())
    assert result.source is StateSource.RECORDED
    assert any("folded on top" in note for note in result.notes)
    ids = [
        block.id
        for message in result.state.messages
        for block in message.content_blocks
        if isinstance(block, ToolUse)
    ]
    assert ids == [READ_USE.id, "toolu_write_2"]


def test_a_disagreement_between_the_fold_and_the_recording_is_reported() -> None:
    """The check that would catch a codec bug or a mishandled reset, rather than
    resuming from a state nobody verified."""
    events = list(two_turn_session())
    end = events[-1]
    assert isinstance(end, TurnEnd) and end.agent_state is not None
    doubled = end.agent_state.with_message(
        Message(role=Role.ASSISTANT, content_blocks=(Text(text="an answer nobody streamed"),))
    )
    events[-1] = replace(end, agent_state=doubled)
    result = replay(events)
    assert any("assistant text differs" in note for note in result.mismatches)
    assert not result.clean


def test_a_resumed_sessions_older_history_is_not_reported_as_a_mismatch() -> None:
    """Reconciliation is a suffix match: a continued session's state legitimately
    contains turns this recording never saw, and reporting that every time would
    train the reader to ignore the list."""
    events = list(two_turn_session())
    end = events[-1]
    assert isinstance(end, TurnEnd) and end.agent_state is not None
    older = ToolUse(id="toolu_from_yesterday", name="read", arguments={"path": "x"})
    with_history = replace(
        end.agent_state,
        messages=(
            Message(role=Role.ASSISTANT, content_blocks=(Text(text="yesterday. "), older)),
            Message(
                role=Role.TOOL,
                content_blocks=(ToolResultBlock(tool_use_id=older.id, content="ok"),),
            ),
            *end.agent_state.messages,
        ),
    )
    events[-1] = replace(end, agent_state=with_history)
    assert replay(events).mismatches == ()


def test_a_tool_result_content_difference_is_not_reported() -> None:
    """The loop truncates result content into the transcript while ``ToolEnd``
    carries it whole; comparing content would fire on every long result."""
    events = list(two_turn_session())
    end = events[-1]
    assert isinstance(end, TurnEnd) and end.agent_state is not None
    truncated = tuple(
        Message(
            role=message.role,
            content_blocks=tuple(
                replace(block, content="…[truncated: 4 chars, 0 lines cut]")
                if isinstance(block, ToolResultBlock)
                else block
                for block in message.content_blocks
            ),
            metadata=message.metadata,
        )
        for message in end.agent_state.messages
    )
    events[-1] = replace(end, agent_state=replace(end.agent_state, messages=truncated))
    assert replay(events).mismatches == ()


# --------------------------------------------------------------------------- #
# The post-crash repair
# --------------------------------------------------------------------------- #


def test_an_unanswered_tool_call_is_answered_with_an_interrupted_block() -> None:
    """The normal shape after a crash. An unpaired ``tool_use`` id is a transcript
    every provider rejects, so replay must not hand one back."""
    result = replay(crashed_session())
    assert result.state.pairing_errors == ()
    assert any("synthesized an interrupted" in note for note in result.repairs)
    tail = result.state.messages[-1]
    assert tail.role is Role.TOOL
    block = tail.content_blocks[-1]
    assert isinstance(block, ToolResultBlock)
    assert block.tool_use_id == "toolu_write_2"
    assert block.is_error is True
    assert block.content == INTERRUPTED_NO_RESULT


def test_a_crash_after_the_tool_returned_keeps_the_real_result() -> None:
    """The result is in the log even though no ``TurnEnd`` checkpointed it; throwing
    it away would re-run a write the model already performed."""
    events = (
        *two_turn_session(),
        *interrupted_tail(),
        ToolEnd(
            tool_use_id="toolu_write_2",
            name="write",
            result=ToolResult(ok=True, content="wrote 2 lines", artifacts=("src/app.py",)),
        ),
    )
    result = replay(events)
    assert result.state.pairing_errors == ()
    assert result.repairs == (), "the fold already answered it; nothing to repair"
    block = result.state.messages[-1].content_blocks[-1]
    assert isinstance(block, ToolResultBlock)
    assert (block.is_error, block.content) == (False, "wrote 2 lines")


def test_a_checkpoint_with_an_unpaired_call_is_repaired_from_the_recorded_result() -> None:
    """Defence for a checkpoint that promised a resumable state and was not one: the
    recorded ``ToolEnd`` answers the call, in preference to an interrupted stub."""
    use = ToolUse(id="t1", name="write", arguments={"path": "src/app.py"})
    unpaired = AgentState(
        messages=(
            Message(role=Role.USER, content_blocks=(Text(text="fix it"),)),
            Message(role=Role.ASSISTANT, content_blocks=(use,)),
        ),
    )
    events = (
        TurnStart(turn_index=0),
        ToolStart(tool_use_id=use.id, name="write", arguments=use.arguments),
        ToolEnd(
            tool_use_id=use.id, name="write", result=ToolResult(ok=True, content="wrote 2")
        ),
        TurnEnd(turn_index=0, state=TurnState.INTERRUPTED, agent_state=unpaired),
    )
    result = replay(events)
    assert result.state.pairing_errors == ()
    assert any("answered from the recorded ToolEnd" in note for note in result.repairs)
    block = result.state.messages[-1].content_blocks[-1]
    assert isinstance(block, ToolResultBlock)
    assert (block.is_error, block.content) == (False, "wrote 2")


def test_a_clean_session_needs_no_repair() -> None:
    result = replay(two_turn_session())
    assert result.repairs == ()
    assert result.state.pairing_errors == ()


def test_unanswered_is_reported_per_turn() -> None:
    turn = fold_turns(interrupted_tail())[0]
    assert turn.unanswered == ("toolu_write_2",)
    assert turn.end is None


# --------------------------------------------------------------------------- #
# Picking a session: --resume and --continue
# --------------------------------------------------------------------------- #


def record(directory: Path, session_id: str, cwd: Path, clock: float) -> None:
    with Transcript.open(
        directory, session_id, model="m-1", cwd=str(cwd), clock=lambda: clock
    ) as transcript:
        transcript.extend(two_turn_session())


def test_continue_picks_the_most_recent_session_in_this_directory(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    here, there = tmp_path / "here", tmp_path / "there"
    here.mkdir()
    there.mkdir()
    record(directory, "old-here", here, 100.0)
    record(directory, "new-there", there, 300.0)
    record(directory, "new-here", here, 200.0)

    assert [row.session_id for row in sessions_for_cwd(directory, str(here))] == [
        "new-here",
        "old-here",
    ]
    continued = resume_latest(directory, str(here))
    assert continued is not None
    assert continued.meta.session_id == "new-here"
    assert continued.state.pairing_errors == ()


def test_continue_in_a_directory_with_no_history_is_none_not_an_empty_session(
    tmp_path: Path,
) -> None:
    """``None`` lets the caller say "nothing to continue here" instead of silently
    starting a fresh conversation that looks resumed."""
    directory = sessions_dir(tmp_path)
    (tmp_path / "here").mkdir()
    record(directory, "elsewhere", tmp_path, 100.0)
    assert resume_latest(directory, str(tmp_path / "here")) is None


def test_cwd_matching_survives_a_symlink_and_a_trailing_slash(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):  # pragma: no cover - platform-dependent
        return
    assert same_cwd(str(real), str(link))
    assert same_cwd(str(real) + "/", str(real))
    directory = sessions_dir(tmp_path)
    record(directory, "s1", link, 100.0)
    assert [row.session_id for row in sessions_for_cwd(directory, str(real))] == ["s1"]


def test_resuming_a_crashed_session_reports_the_torn_line_and_still_replays(
    tmp_path: Path,
) -> None:
    directory = sessions_dir(tmp_path)
    with Transcript.open(directory, "s1", model="m-1", cwd=str(tmp_path)) as transcript:
        transcript.extend(crashed_session())
        path = transcript.path
    truncate_mid_line(path)

    resumed = resume(directory, "s1")
    assert len(resumed.skipped) == 1
    assert resumed.meta.model == "m-1"
    assert resumed.state.pairing_errors == ()
    assert resumed.replay.source is StateSource.RECORDED

    read, replayed = load_session(path)
    assert read.skipped == resumed.skipped
    assert replayed.state == resumed.state


def test_resuming_an_id_that_does_not_exist_fails_by_name(tmp_path: Path) -> None:
    """A typo in ``--resume <id>`` must not open an empty session that looks resumed."""
    directory = sessions_dir(tmp_path)
    directory.mkdir(parents=True)
    with pytest.raises(TranscriptError, match="never-existed"):
        resume(directory, "never-existed")


def test_the_replayed_state_keeps_todos_mode_and_checkpoint(tmp_path: Path) -> None:
    """Everything ``--resume`` needs to continue, not just the messages."""
    state = AgentState(
        messages=(Message(role=Role.USER, content_blocks=(Text(text="hi"),)),),
        todos=(Todo(id="1", subject="ship it"),),
        cwd=str(tmp_path),
        mode=Mode.FULL,
        checkpoint_id="ckpt-7",
    )
    events = (
        TurnStart(turn_index=0),
        TextDelta(text="ok"),
        TurnEnd(
            turn_index=0,
            state=TurnState.DONE,
            agent_state=state.with_message(
                Message(role=Role.ASSISTANT, content_blocks=(Text(text="ok"),))
            ),
        ),
    )
    result = replay(events)
    assert result.state.mode is Mode.FULL
    assert result.state.todos == state.todos
    assert result.state.checkpoint_id == "ckpt-7"


def test_a_tool_result_recorded_as_an_error_stays_an_error(tmp_path: Path) -> None:
    events = (
        TurnStart(turn_index=0),
        ToolStart(tool_use_id="t1", name="read"),
        ToolEnd(
            tool_use_id="t1", name="read", result=ToolResult(ok=False, error="ENOENT")
        ),
    )
    result = replay(events)
    block = result.state.messages[-1].content_blocks[0]
    assert isinstance(block, ToolResultBlock)
    assert (block.is_error, block.content) == (True, "ENOENT")


def test_the_read_results_artifacts_do_not_leak_into_the_transcript() -> None:
    """Artifacts are metadata for the picker, not content for the model."""
    result = replay((TurnStart(turn_index=0),
                     ToolStart(tool_use_id=READ_USE.id, name="read"),
                     ToolEnd(tool_use_id=READ_USE.id, name="read", result=READ_RESULT)))
    block = result.state.messages[-1].content_blocks[0]
    assert isinstance(block, ToolResultBlock)
    assert block.content == READ_RESULT.content
