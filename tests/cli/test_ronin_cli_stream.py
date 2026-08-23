"""What ``ronin.cli.stream`` has to get right for a turn to be a session.

Each test here names one of the five things the middleware does, and asserts the
*failure mode* rather than the happy path: that the pinned prefix is what pushed
compaction over its trigger, that a read-only turn does not checkpoint, that the
verify failure arrived on a ``TOOL``-role message and on no ``USER``-role message,
that the repair loop stops and says so, and that a cancel gets out.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
import stream_harness as h

from ronin.cli.spine import Runtime
from ronin.cli.stream import (
    CHECKPOINT_STEP,
    COMPACT_STEP,
    VERIFY_STEP,
    CompactionPairingError,
    Conversation,
    RewindOutcome,
    plan_runtime,
    run_prompt,
)
from ronin.context.compaction import COMPACTION_KEY
from ronin.core.types import (
    Error,
    Event,
    Message,
    Role,
    Text,
    ToolEnd,
    ToolStart,
    TurnEnd,
    unpaired_tool_uses,
)
from ronin.persistence.transcript import Transcript, read_events
from ronin.verify.repair import RepairVerdict
from ronin.verify.runner import run_command

# The five-section shape ``context.compaction`` requires of a summary. Returned by
# the fake summarizer so ``missing_sections`` stays empty and the test is about
# compaction firing, not about a malformed digest.
SUMMARY = """\
## Goal
keep working

## Files touched
- src/widget.py — edited

## Learned
the module is small

## Failed
nothing yet

## Remaining
finish the change
"""


async def summarize(prompt: str) -> str:
    del prompt
    return SUMMARY


def steps(events: list[Event], name: str) -> list[ToolEnd]:
    """Every completed middleware step called ``name``."""
    return [e for e in events if isinstance(e, ToolEnd) and e.name == name]


def started(events: list[Event], name: str) -> list[ToolStart]:
    return [e for e in events if isinstance(e, ToolStart) and e.name == name]


# --------------------------------------------------------------------------- #
# Compaction
# --------------------------------------------------------------------------- #


async def test_compaction_fires_because_the_pinned_prefix_is_counted(tmp_path: Path) -> None:
    # The transcript on its own is nowhere near the trigger; the repo map and
    # RONIN.md are. If ``pinned_prefix_tokens`` were left at its default of 0 —
    # the bug ``spine`` exists to prevent — nothing here would compact.
    loaded = h.build_loaded(tmp_path, memory_text="remember this\n" * 40, repo_map_tokens=1_500)
    runtime = h.build_runtime(loaded, context_window=2_000)
    pinned = loaded.pinned_prefix_tokens()
    assert pinned > 0

    conversation = Conversation(model=h.ScriptedModel([h.say("first"), h.say("second")]))
    conversation.messages = tuple(
        Message(role=Role.USER, content_blocks=(Text("a request"),)) for _ in range(6)
    )
    await h.collect(conversation.run_prompt(runtime, "go", summarizer=summarize))

    assert conversation.last_pinned_prefix_tokens == pinned
    assert conversation.compactions == 1
    result = conversation.last_compaction
    assert result is not None and result.compacted


async def test_the_compaction_marker_reaches_the_event_stream(tmp_path: Path) -> None:
    loaded = h.build_loaded(tmp_path, repo_map_tokens=1_500)
    runtime = h.build_runtime(loaded, context_window=2_000)
    conversation = Conversation(model=h.ScriptedModel([h.say("ok")]))
    conversation.messages = tuple(
        Message(role=Role.USER, content_blocks=(Text("history " * 20),)) for _ in range(6)
    )

    events = await h.collect(conversation.run_prompt(runtime, "go", summarizer=summarize))

    marks = steps(events, COMPACT_STEP)
    assert len(marks) == 1
    assert "context compacted" in marks[0].result.content
    # The same marker is inside the transcript the model sees, on the summary message.
    folded = [m for m in conversation.messages if m.metadata.get(COMPACTION_KEY)]
    assert folded and marks[0].result.content in folded[0].text


async def test_no_compaction_below_the_trigger_emits_no_step(tmp_path: Path) -> None:
    loaded = h.build_loaded(tmp_path)
    runtime = h.build_runtime(loaded, context_window=100_000)
    conversation = Conversation(model=h.ScriptedModel([h.say("ok")]))

    events = await h.collect(conversation.run_prompt(runtime, "go", summarizer=summarize))

    assert steps(events, COMPACT_STEP) == []
    assert conversation.compactions == 0


async def test_compaction_across_a_tool_call_leaves_no_unpaired_tool_use(
    tmp_path: Path,
) -> None:
    # A fold whose boundary lands between a ToolUse and its ToolResultBlock is the
    # 400 the provider raises on the *next* turn. Ten turns of real tool calls with
    # a window this small guarantees the boundary lands inside one of them.
    loaded = h.build_loaded(tmp_path, repo_map_tokens=200)
    tools = h.ScriptedTools([h.reader(content="body " * 200)])
    runtime = h.build_runtime(loaded, tools=tools, context_window=1_500)
    responses: list[object] = []
    for turn in range(10):
        responses.append(h.call("read", {"file_path": f"m{turn}.py"}, use_id=f"u{turn}"))
        responses.append(h.say(f"turn {turn}"))
    conversation = Conversation(model=h.ScriptedModel(responses))  # type: ignore[arg-type]

    for turn in range(10):
        await h.collect(
            conversation.run_prompt(runtime, f"step {turn}", summarizer=summarize, verify=False)
        )
        assert unpaired_tool_uses(conversation.messages) == ()

    assert conversation.compactions > 0


def test_a_pairing_error_names_the_ids_it_found() -> None:
    error = CompactionPairingError(["u1", "u2"])
    assert error.unpaired == ("u1", "u2")
    assert "u1" in str(error) and "400" in str(error)


# --------------------------------------------------------------------------- #
# Checkpoints
# --------------------------------------------------------------------------- #


async def test_a_read_only_turn_takes_no_checkpoint(tmp_path: Path) -> None:
    root = h.git_repo(tmp_path)
    git = h.FakeGit()
    runtime = h.build_runtime(h.build_loaded(root), tools=h.ScriptedTools([h.reader()]), git=git)
    model = h.ScriptedModel([h.call("read", {"file_path": "a.py"}), h.say("done")])

    events = await h.collect(Conversation(model=model).run_prompt(runtime, "look"))

    assert steps(events, CHECKPOINT_STEP) == []
    assert git.checkpoints_taken == 0


async def test_a_mutating_turn_checkpoints_before_the_edit_lands(tmp_path: Path) -> None:
    root = h.git_repo(tmp_path)
    target = root / "src" / "widget.py"
    target.parent.mkdir(parents=True)
    target.write_text("original\n", encoding="utf-8")
    seen: list[str] = []
    git = h.FakeGit(on_commit=lambda: seen.append(target.read_text(encoding="utf-8")))
    runtime = h.build_runtime(
        h.build_loaded(root), tools=h.ScriptedTools([h.writer(root)]), git=git
    )
    model = h.ScriptedModel(
        [h.call("edit", {"file_path": "src/widget.py", "content": "changed\n"}), h.say("done")]
    )

    events = await h.collect(Conversation(model=model).run_prompt(runtime, "edit it", verify=False))

    assert git.checkpoints_taken == 1
    # The file still held its original bytes when the snapshot was taken. A
    # checkpoint taken after the edit preserves the damage, which is the whole
    # reason this is asserted on content rather than on event order.
    assert seen == ["original\n"]
    assert target.read_text(encoding="utf-8") == "changed\n"
    assert started(events, CHECKPOINT_STEP)[0].arguments["before"] == "edit"


async def test_only_one_checkpoint_per_turn_however_many_edits(tmp_path: Path) -> None:
    root = h.git_repo(tmp_path)
    git = h.FakeGit()
    runtime = h.build_runtime(
        h.build_loaded(root), tools=h.ScriptedTools([h.writer(root)]), git=git
    )
    model = h.ScriptedModel(
        [
            h.call("edit", {"file_path": "a.py", "content": "1"}, use_id="e1"),
            h.call("edit", {"file_path": "b.py", "content": "2"}, use_id="e2"),
            h.say("done"),
        ]
    )

    await h.collect(Conversation(model=model).run_prompt(runtime, "edit both", verify=False))

    assert git.checkpoints_taken == 1


async def test_unavailable_checkpoints_are_a_note_not_a_failure(tmp_path: Path) -> None:
    # No ``.git`` at all: the store degrades with a reason a human can act on.
    runtime = h.build_runtime(h.build_loaded(tmp_path), tools=h.ScriptedTools([h.writer(tmp_path)]))
    model = h.ScriptedModel([h.call("edit", {"file_path": "a.py", "content": "1"}), h.say("ok")])
    conversation = Conversation(model=model)

    events = await h.collect(conversation.run_prompt(runtime, "edit", verify=False))

    end = [e for e in events if isinstance(e, TurnEnd)][-1]
    assert end.stop_reason == "no_tool_calls"
    assert not [e for e in events if isinstance(e, Error)]
    step = steps(events, CHECKPOINT_STEP)[0]
    assert step.result.ok is True
    assert "git init" in step.result.content
    assert any("no checkpoint" in note for note in conversation.notes)


async def test_a_missing_git_binary_is_reported_and_survived(tmp_path: Path) -> None:
    root = h.git_repo(tmp_path)
    runtime = h.build_runtime(
        h.build_loaded(root),
        tools=h.ScriptedTools([h.writer(root)]),
        git=h.missing_git(),
    )
    model = h.ScriptedModel([h.call("edit", {"file_path": "a.py", "content": "1"}), h.say("ok")])
    conversation = Conversation(model=model)

    events = await h.collect(conversation.run_prompt(runtime, "edit", verify=False))

    assert "git is not installed" in steps(events, CHECKPOINT_STEP)[0].result.content
    assert conversation.notes


# --------------------------------------------------------------------------- #
# Transcript
# --------------------------------------------------------------------------- #


async def test_the_transcript_records_every_event(tmp_path: Path) -> None:
    root = h.git_repo(tmp_path)
    transcript = Transcript.open(root / ".ronin" / "sessions", "s1", cwd=str(root))
    runtime = h.build_runtime(
        h.build_loaded(root),
        tools=h.ScriptedTools([h.reader()]),
        transcript=transcript,
    )
    model = h.ScriptedModel([h.call("read", {"file_path": "a.py"}), h.say("answer")])

    events = await h.collect(Conversation(model=model).run_prompt(runtime, "look"))
    transcript.close()

    recorded = read_events(transcript.path)
    assert recorded.skipped == ()
    assert len(recorded.events) == len(events)
    assert [type(e).__name__ for e in recorded.events] == [type(e).__name__ for e in events]


async def test_a_transcript_that_cannot_be_written_does_not_end_the_session(
    tmp_path: Path,
) -> None:
    root = h.git_repo(tmp_path)
    transcript = Transcript.open(root / ".ronin" / "sessions", "s1", cwd=str(root))
    transcript.close()  # every subsequent append raises TranscriptError
    runtime = h.build_runtime(
        h.build_loaded(root), tools=h.ScriptedTools([h.reader()]), transcript=transcript
    )
    model = h.ScriptedModel([h.say("still answered")])
    conversation = Conversation(model=model)

    events = await h.collect(conversation.run_prompt(runtime, "hello"))

    assert [e for e in events if isinstance(e, TurnEnd)][-1].state.value == "done"
    assert any("stopped recording" in note for note in conversation.notes)
    # Told once, not once per event.
    assert sum("stopped recording" in note for note in conversation.notes) == 1


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #


def verify_setup(tmp_path: Path) -> tuple[Path, h.FakeGit, h.ScriptedTools]:
    root = h.git_repo(tmp_path)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "widget.py").write_text("x\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_widget.py").write_text("x\n", encoding="utf-8")
    return root, h.FakeGit(diff=h.DIFF_ONE_FILE), h.ScriptedTools([h.writer(root)])


def edits(count: int) -> list[object]:
    out: list[object] = []
    for index in range(count):
        out.append(
            h.call(
                "edit", {"file_path": "src/widget.py", "content": str(index)}, use_id=f"e{index}"
            )
        )
        out.append(h.say(f"attempt {index}"))
    return out


async def test_a_verify_failure_arrives_as_a_tool_result_and_not_as_a_user_message(
    tmp_path: Path,
) -> None:
    root, git, tools = verify_setup(tmp_path)
    runtime = h.build_runtime(h.build_loaded(root, verify=h.pytest_spec()), tools=tools, git=git)
    commands = h.ScriptedCommands(outputs=[(1, h.PYTEST_FAILURE)])
    conversation = Conversation(model=h.ScriptedModel(edits(6)), command=commands)  # type: ignore[arg-type]

    events = await h.collect(conversation.run_prompt(runtime, "fix widget"))

    verify = steps(events, VERIFY_STEP)
    assert verify and verify[0].result.ok is False
    assert "verify failed" in verify[0].result.error

    carrying = [
        message
        for message in conversation.messages
        if any("verify failed" in getattr(block, "content", "") for block in message.content_blocks)
    ]
    assert carrying, "the failure never reached the transcript"
    # The whole point of ``as_tool_result``: on a TOOL message the failure is an
    # observation about what the model just did; on a USER message it reads as a
    # fresh instruction and the model re-scopes to explaining the traceback.
    assert {message.role for message in carrying} == {Role.TOOL}
    assert all(message.role is not Role.USER for message in carrying)


async def test_the_repair_loop_stops_at_its_cap_with_an_honest_report(
    tmp_path: Path,
) -> None:
    root, git, tools = verify_setup(tmp_path)
    runtime = h.build_runtime(h.build_loaded(root, verify=h.pytest_spec()), tools=tools, git=git)
    # The same failure with cosmetic differences: a different tmpdir, a line number
    # that moved, a different duration. The signature must read them as one.
    commands = h.ScriptedCommands(outputs=[(1, h.PYTEST_FAILURE), (1, h.PYTEST_FAILURE_AGAIN)])
    conversation = Conversation(model=h.ScriptedModel(edits(8)), command=commands)  # type: ignore[arg-type]

    events = await h.collect(conversation.run_prompt(runtime, "fix widget"))

    report = conversation.last_repair
    assert report is not None
    assert report.verdict is RepairVerdict.STUCK
    assert not report.fixed
    errors = [e for e in events if isinstance(e, Error)]
    assert len(errors) == 1
    assert errors[0].kind == VERIFY_STEP
    assert "i could not fix this" in errors[0].message
    assert "what i tried" in errors[0].message
    assert "what i think is wrong" in errors[0].message
    # Bounded: three verify runs (baseline plus one per attempt), not an open loop.
    assert commands.runs == 3


async def test_a_verify_pass_that_goes_green_reports_fixed_and_no_error(
    tmp_path: Path,
) -> None:
    root, git, tools = verify_setup(tmp_path)
    runtime = h.build_runtime(h.build_loaded(root, verify=h.pytest_spec()), tools=tools, git=git)
    commands = h.ScriptedCommands(outputs=[(1, h.PYTEST_FAILURE), (0, "2 passed")])
    conversation = Conversation(model=h.ScriptedModel(edits(4)), command=commands)  # type: ignore[arg-type]

    events = await h.collect(conversation.run_prompt(runtime, "fix widget"))

    report = conversation.last_repair
    assert report is not None and report.verdict is RepairVerdict.FIXED
    assert [e for e in events if isinstance(e, Error)] == []


async def test_a_green_first_pass_runs_no_repair_turn(tmp_path: Path) -> None:
    root, git, tools = verify_setup(tmp_path)
    runtime = h.build_runtime(h.build_loaded(root, verify=h.pytest_spec()), tools=tools, git=git)
    commands = h.ScriptedCommands(outputs=[(0, "2 passed")])
    model = h.ScriptedModel(edits(1))  # type: ignore[arg-type]
    conversation = Conversation(model=model, command=commands)

    events = await h.collect(conversation.run_prompt(runtime, "fix widget"))

    assert model.turns == 2, "a green verify must not buy the model another turn"
    assert steps(events, VERIFY_STEP)[0].result.ok is True
    report = conversation.last_repair
    assert report is not None and report.verdict is RepairVerdict.ALREADY_GREEN


async def test_a_repo_with_no_verify_command_says_nothing_was_proven(
    tmp_path: Path,
) -> None:
    root, git, tools = verify_setup(tmp_path)
    runtime = h.build_runtime(h.build_loaded(root), tools=tools, git=git)  # empty VerifySpec
    commands = h.ScriptedCommands(outputs=[(0, "")])
    conversation = Conversation(model=h.ScriptedModel(edits(1)), command=commands)  # type: ignore[arg-type]

    events = await h.collect(conversation.run_prompt(runtime, "edit"))

    result = steps(events, VERIFY_STEP)[0].result
    assert "Nothing was proven" in result.content
    assert commands.runs == 0
    assert any("no lint, typecheck, test or build command" in n for n in conversation.notes)


async def test_ask_mode_skips_verification_and_says_so(tmp_path: Path) -> None:
    root, git, tools = verify_setup(tmp_path)
    from ronin.core.types import Mode

    runtime = h.build_runtime(
        h.build_loaded(root, mode=Mode.ASK, verify=h.pytest_spec()), tools=tools, git=git
    )
    commands = h.ScriptedCommands(outputs=[(0, "")])
    conversation = Conversation(model=h.ScriptedModel(edits(1)), command=commands)  # type: ignore[arg-type]

    events = await h.collect(conversation.run_prompt(runtime, "edit"))

    assert commands.runs == 0
    assert "do not report it as verified" in steps(events, VERIFY_STEP)[0].result.content


async def test_verify_false_runs_nothing_at_all(tmp_path: Path) -> None:
    root, git, tools = verify_setup(tmp_path)
    runtime = h.build_runtime(h.build_loaded(root, verify=h.pytest_spec()), tools=tools, git=git)
    commands = h.ScriptedCommands(outputs=[(1, h.PYTEST_FAILURE)])
    conversation = Conversation(model=h.ScriptedModel(edits(1)), command=commands)  # type: ignore[arg-type]

    events = await h.collect(conversation.run_prompt(runtime, "edit", verify=False))

    assert commands.runs == 0
    assert steps(events, VERIFY_STEP) == []


# --------------------------------------------------------------------------- #
# Cancellation, budget, plan mode
# --------------------------------------------------------------------------- #


async def test_cancelled_error_propagates_out_of_run_prompt(tmp_path: Path) -> None:
    model = h.HangingModel()
    runtime = h.build_runtime(h.build_loaded(tmp_path))

    async def drive() -> None:
        async for _event in Conversation(model=model).run_prompt(runtime, "go"):
            pass

    task = asyncio.create_task(drive())
    await asyncio.wait_for(model.started.wait(), timeout=5.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_wall_clock_is_folded_into_the_budget_at_the_turn_boundary(
    tmp_path: Path,
) -> None:
    # Nothing in the loop advances ``Budget.elapsed_seconds``, so a wall-clock
    # ceiling is a ceiling nothing enforces unless the turn boundary advances it.
    ticks = iter([100.0, 100.0, 130.0, 160.0])
    runtime = h.build_runtime(h.build_loaded(tmp_path))
    conversation = Conversation(
        model=h.ScriptedModel([h.say("one"), h.say("two")]), clock=lambda: next(ticks)
    )

    await h.collect(conversation.run_prompt(runtime, "first"))
    await h.collect(conversation.run_prompt(runtime, "second"))

    assert conversation.budget.elapsed_seconds > 0


async def test_run_prompt_is_one_shot_and_a_conversation_is_not(tmp_path: Path) -> None:
    runtime = h.build_runtime(h.build_loaded(tmp_path))
    model = h.ScriptedModel([h.say("a"), h.say("b")])

    await h.collect(run_prompt(runtime, "first"))
    await h.collect(run_prompt(runtime, "second"))
    # Two one-shot calls: neither saw the other's messages.
    assert len(model.calls) == 0  # the module-level default model was never used
    conversation = Conversation(model=model)
    await h.collect(conversation.run_prompt(runtime, "first"))
    await h.collect(conversation.run_prompt(runtime, "second"))
    assert len(model.calls[1].messages) > len(model.calls[0].messages)


async def test_plan_mode_removes_every_mutating_tool_from_the_registry(
    tmp_path: Path,
) -> None:
    from ronin.tools.base import ToolContext
    from ronin.tools.registry import build_registry

    root = tmp_path
    base = build_registry(ToolContext(root=root))
    runtime = h.build_runtime(h.build_loaded(root))
    from dataclasses import replace as dataclass_replace

    from ronin.session import Session

    session: Session = dataclass_replace(runtime.session, registry=base)
    runtime = dataclass_replace(runtime, session=session, registry=base)

    planning = plan_runtime(runtime)

    names = {spec.name for spec in planning.registry.specs()}
    assert "write" not in names and "edit" not in names and "bash" not in names
    assert all(spec.danger_level.value == 0 for spec in planning.registry.specs())
    assert "PLAN MODE" in planning.system


# --------------------------------------------------------------------------- #
# Rewind (esc esc) — truncate the conversation and restore the turn's checkpoint
# --------------------------------------------------------------------------- #


async def test_rewind_with_no_turn_yet_reports_nothing_to_do(tmp_path: Path) -> None:
    root = h.git_repo(tmp_path)
    runtime = h.build_runtime(
        h.build_loaded(root), tools=h.ScriptedTools([h.reader()]), git=h.FakeGit()
    )

    outcome = await Conversation().rewind(runtime)

    assert isinstance(outcome, RewindOutcome)
    assert outcome.ok is False
    assert outcome.messages_dropped == 0
    assert "nothing to rewind" in outcome.detail


async def test_rewind_of_a_mutating_turn_truncates_messages_and_restores(
    tmp_path: Path,
) -> None:
    root = h.git_repo(tmp_path)
    git = h.FakeGit()
    runtime = h.build_runtime(
        h.build_loaded(root), tools=h.ScriptedTools([h.writer(root)]), git=git
    )
    model = h.ScriptedModel([h.call("edit", {"file_path": "a.py", "content": "1"}), h.say("done")])
    conversation = Conversation(model=model)

    await h.collect(conversation.run_prompt(runtime, "edit it", verify=False))
    assert conversation.messages, "the turn left messages to truncate"
    assert len(conversation.checkpoints) == 1
    checkpoints_before = git.checkpoints_taken

    outcome = await conversation.rewind(runtime)

    assert outcome.ok is True and outcome.files_restored is True
    # Back to before the (only) turn: both the transcript and the checkpoint list.
    assert conversation.messages == ()
    assert conversation.checkpoints == []
    # A restore is a reset+clean, not another checkpoint commit.
    assert git.checkpoints_taken == checkpoints_before
    assert any("reset" in " ".join(args) for args in git.argv_log)
    assert any("clean" in " ".join(args) for args in git.argv_log)


async def test_rewind_of_a_read_only_turn_drops_messages_and_restores_nothing(
    tmp_path: Path,
) -> None:
    root = h.git_repo(tmp_path)
    git = h.FakeGit()
    runtime = h.build_runtime(h.build_loaded(root), tools=h.ScriptedTools([h.reader()]), git=git)
    model = h.ScriptedModel([h.call("read", {"file_path": "a.py"}), h.say("done")])
    conversation = Conversation(model=model)

    await h.collect(conversation.run_prompt(runtime, "look", verify=False))
    assert conversation.messages
    assert conversation.checkpoints == [], "a read-only turn takes no checkpoint"

    outcome = await conversation.rewind(runtime)

    assert outcome.ok is True and outcome.files_restored is False
    assert conversation.messages == ()
    assert "no checkpoint was taken" in outcome.detail
    # Nothing to restore, so no reset/clean was ever issued.
    assert not any("reset" in " ".join(args) for args in git.argv_log)


async def test_rewind_degrades_to_conversation_only_without_git(tmp_path: Path) -> None:
    # No ``.git`` at all: a mutating turn cannot checkpoint, so its files cannot be put
    # back — the conversation still rewinds and the outcome says the files remain.
    runtime = h.build_runtime(h.build_loaded(tmp_path), tools=h.ScriptedTools([h.writer(tmp_path)]))
    model = h.ScriptedModel([h.call("edit", {"file_path": "a.py", "content": "1"}), h.say("ok")])
    conversation = Conversation(model=model)

    await h.collect(conversation.run_prompt(runtime, "edit", verify=False))
    assert conversation.checkpoints == []
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "1"

    outcome = await conversation.rewind(runtime)

    assert outcome.ok is True and outcome.files_restored is False
    assert conversation.messages == ()
    assert "checkpoints are off" in outcome.detail
    # The file the turn wrote is untouched: an honest degradation restores nothing.
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "1"


async def test_rewind_is_one_turn_back_and_stacks_to_the_start(tmp_path: Path) -> None:
    root = h.git_repo(tmp_path)
    git = h.FakeGit()
    runtime = h.build_runtime(h.build_loaded(root), tools=h.ScriptedTools([h.reader()]), git=git)
    model = h.ScriptedModel(
        [
            h.call("read", {"file_path": "a.py"}),
            h.say("one"),
            h.call("read", {"file_path": "b.py"}),
            h.say("two"),
        ]
    )
    conversation = Conversation(model=model)

    await h.collect(conversation.run_prompt(runtime, "first", verify=False))
    after_first = conversation.messages
    await h.collect(conversation.run_prompt(runtime, "second", verify=False))
    assert len(conversation.messages) > len(after_first)

    second = await conversation.rewind(runtime)
    assert second.ok is True
    assert conversation.messages == after_first, "exactly one turn back, not the whole session"

    first = await conversation.rewind(runtime)
    assert first.ok is True
    assert conversation.messages == (), "a second rewind lands on the start"

    exhausted = await conversation.rewind(runtime)
    assert exhausted.ok is False, "and a third has nothing left to rewind"


async def test_a_turn_after_a_rewind_continues_from_the_truncated_transcript(
    tmp_path: Path,
) -> None:
    # The point of truncating rather than view-only: the model's next turn must not see
    # the rewound turn's messages, or the rewind changed nothing it can act on.
    root = h.git_repo(tmp_path)
    runtime = h.build_runtime(
        h.build_loaded(root), tools=h.ScriptedTools([h.reader()]), git=h.FakeGit()
    )
    model = h.ScriptedModel([h.say("one"), h.say("two")])
    conversation = Conversation(model=model)

    await h.collect(conversation.run_prompt(runtime, "first", verify=False))
    await conversation.rewind(runtime)
    await h.collect(conversation.run_prompt(runtime, "second", verify=False))

    # The second turn's request carried exactly one user message — the rewound "first"
    # is gone from what the model was handed.
    second_request = model.calls[-1].messages
    user_texts = [
        block.text
        for message in second_request
        if message.role is Role.USER
        for block in message.content_blocks
        if isinstance(block, Text)
    ]
    assert user_texts == ["second"]


@pytest.mark.skipif(shutil.which("git") is None, reason="uses a real local git")
# --------------------------------------------------------------------------- #
# a rewind moves the transcript AND the work tree; file state must follow both
# --------------------------------------------------------------------------- #


def _gated_runtime(runtime: Runtime) -> Runtime:
    """The harness wires scripted tools straight in; a rewind test needs the gate.

    ``runtime.files`` is otherwise a tracker nothing is attached to, so a test against
    it would prove only that an unused object stayed unused.
    """
    from dataclasses import replace as _replace

    from ronin.cli.gate import gated

    # No `taint=`: the gate refuses a tracker the PolicyEngine does not share, and the
    # harness's engine has none. It adopts the engine's, which is the invariant.
    return _replace(runtime, registry=gated(runtime.registry, runtime.policy, files=runtime.files))


async def test_a_rewind_forgets_reads_it_dropped_from_the_transcript(tmp_path: Path) -> None:
    """The same divergence compaction gets repaired for, on the other shrinking path.

    Without this the record outlives the read: the guard calls the file safe to edit
    against content no longer in front of the model, and a repeat read is answered
    with "scroll back for the contents".
    """
    root = h.git_repo(tmp_path)
    target = root / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    runtime = _gated_runtime(
        h.build_runtime(h.build_loaded(root), tools=h.ScriptedTools([h.reader()]), git=h.FakeGit())
    )
    runtime.files.record_read(target)
    conversation = Conversation(model=h.ScriptedModel([h.say("read it")]))

    await h.collect(conversation.run_prompt(runtime, "look at a.py", verify=False))
    assert runtime.files.recorded(target) is not None

    outcome = await conversation.rewind(runtime)

    assert outcome.ok is True
    assert conversation.messages == ()
    # No read survives in the transcript, so no record may survive either.
    assert runtime.files.recorded(target) is None


async def test_a_restore_forgets_every_record_because_the_tree_moved(tmp_path: Path) -> None:
    """Truncating the transcript is only half of a rewind.

    The read of ``kept.py`` happens in the first turn, so it survives the truncation
    and the transcript sync has every reason to keep its record. The restore is what
    invalidates it: a checkpoint moves files under *every* record at once, including
    files the rewound turn never touched. Digests are re-established by reading, so
    the honest state after a restore is none of them.

    Written this way deliberately — an earlier version read nothing in a surviving
    turn, so the sync cleared the record first and the test passed with this fix
    disabled.
    """
    root = tmp_path
    await run_command(["git", "init", "--quiet"], cwd=root)
    edited = root / "poem.txt"
    untouched = root / "kept.py"
    edited.write_text("before\n", encoding="utf-8")
    untouched.write_text("z = 3\n", encoding="utf-8")
    runtime = _gated_runtime(
        h.build_runtime(
            h.build_loaded(root),
            tools=h.ScriptedTools([h.reader(), h.writer(root)]),
        )
    )
    conversation = Conversation(
        model=h.ScriptedModel(
            [
                h.call("read", {"path": str(untouched)}),
                h.say("seen"),
                h.call("edit", {"file_path": "poem.txt", "content": "after\n"}),
                h.say("done"),
            ]
        )
    )

    await h.collect(conversation.run_prompt(runtime, "read kept.py", verify=False))
    assert runtime.files.recorded(untouched) is not None
    await h.collect(conversation.run_prompt(runtime, "rewrite the poem", verify=False))

    outcome = await conversation.rewind(runtime)

    assert outcome.ok is True and outcome.files_restored is True
    assert edited.read_text(encoding="utf-8") == "before\n"
    # kept.py's read is still in the transcript, so the sync alone would keep it.
    assert runtime.files.known_paths() == ()
    assert "read again" in outcome.detail


async def test_a_rewind_that_restores_nothing_keeps_a_read_that_survived(
    tmp_path: Path,
) -> None:
    """The control. Forgetting on every rewind would be the too-generous direction.

    The read happens in the *first* turn, so it is still in the transcript after the
    second is rewound, and a read-only turn takes no checkpoint so nothing on disk
    moved. Both halves of the justification still hold, so the record must survive.
    """
    root = h.git_repo(tmp_path)
    survivor = root / "kept.py"
    survivor.write_text("z = 3\n", encoding="utf-8")
    runtime = _gated_runtime(
        h.build_runtime(h.build_loaded(root), tools=h.ScriptedTools([h.reader()]), git=h.FakeGit())
    )
    conversation = Conversation(
        model=h.ScriptedModel([h.call("read", {"path": str(survivor)}), h.say("one"), h.say("two")])
    )

    await h.collect(conversation.run_prompt(runtime, "read kept.py", verify=False))
    assert runtime.files.recorded(survivor) is not None, "the gate recorded the read"
    await h.collect(conversation.run_prompt(runtime, "second turn", verify=False))

    outcome = await conversation.rewind(runtime)

    assert outcome.ok is True and outcome.files_restored is False
    assert runtime.files.recorded(survivor) is not None


async def test_a_rewind_survives_a_registry_that_cannot_answer(tmp_path: Path) -> None:
    # The harness's own scripted registry has neither method. A rewind is a recovery
    # action; it must not be the thing that raises.
    root = h.git_repo(tmp_path)
    runtime = h.build_runtime(
        h.build_loaded(root), tools=h.ScriptedTools([h.reader()]), git=h.FakeGit()
    )
    conversation = Conversation(model=h.ScriptedModel([h.say("hi")]))
    await h.collect(conversation.run_prompt(runtime, "say hi", verify=False))

    outcome = await conversation.rewind(runtime)

    assert outcome.ok is True


async def test_rewind_really_reverts_a_file_on_disk(tmp_path: Path) -> None:
    # The integration proof: a real shadow git repo, a real file, a real restore. The
    # unit tests above use FakeGit for the branching; this one shows the bytes move.
    root = tmp_path
    await run_command(["git", "init", "--quiet"], cwd=root)
    target = root / "poem.txt"
    target.write_text("before\n", encoding="utf-8")
    runtime = h.build_runtime(h.build_loaded(root), tools=h.ScriptedTools([h.writer(root)]))
    model = h.ScriptedModel(
        [h.call("edit", {"file_path": "poem.txt", "content": "after\n"}), h.say("done")]
    )
    conversation = Conversation(model=model)

    await h.collect(conversation.run_prompt(runtime, "rewrite the poem", verify=False))
    assert target.read_text(encoding="utf-8") == "after\n"
    assert len(conversation.checkpoints) == 1

    outcome = await conversation.rewind(runtime)

    assert outcome.ok is True and outcome.files_restored is True
    assert target.read_text(encoding="utf-8") == "before\n", "the edit is gone from disk"
    assert conversation.messages == ()
