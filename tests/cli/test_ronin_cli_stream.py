"""What ``ronin.cli.stream`` has to get right for a turn to be a session.

Each test here names one of the five things the middleware does, and asserts the
*failure mode* rather than the happy path: that the pinned prefix is what pushed
compaction over its trigger, that a read-only turn does not checkpoint, that the
verify failure arrived on a ``TOOL``-role message and on no ``USER``-role message,
that the repair loop stops and says so, and that a cancel gets out.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import stream_harness as h

from ronin.cli.stream import (
    CHECKPOINT_STEP,
    COMPACT_STEP,
    VERIFY_STEP,
    CompactionPairingError,
    Conversation,
    plan_runtime,
    run_prompt,
)
from ronin.context.compaction import COMPACTION_KEY
from ronin.core.types import (
    Error,
    Event,
    Message,
    Role,
    ToolEnd,
    ToolStart,
    TurnEnd,
    unpaired_tool_uses,
)
from ronin.persistence.transcript import Transcript, read_events
from ronin.verify.repair import RepairVerdict

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
    loaded = h.build_loaded(
        tmp_path, memory_text="remember this\n" * 40, repo_map_tokens=1_500
    )
    runtime = h.build_runtime(loaded, context_window=2_000)
    pinned = loaded.pinned_prefix_tokens()
    assert pinned > 0

    conversation = Conversation(model=h.ScriptedModel([h.say("first"), h.say("second")]))
    conversation.messages = tuple(
        Message(role=Role.USER, content_blocks=(h.Text("a request"),)) for _ in range(6)
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
        Message(role=Role.USER, content_blocks=(h.Text("history " * 20),)) for _ in range(6)
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
    runtime = h.build_runtime(
        h.build_loaded(root), tools=h.ScriptedTools([h.reader()]), git=git
    )
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

    events = await h.collect(
        Conversation(model=model).run_prompt(runtime, "edit it", verify=False)
    )

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
    runtime = h.build_runtime(
        h.build_loaded(tmp_path), tools=h.ScriptedTools([h.writer(tmp_path)])
    )
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
    runtime = h.build_runtime(
        h.build_loaded(root, verify=h.pytest_spec()), tools=tools, git=git
    )
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
    runtime = h.build_runtime(
        h.build_loaded(root, verify=h.pytest_spec()), tools=tools, git=git
    )
    # The same failure with cosmetic differences: a different tmpdir, a line number
    # that moved, a different duration. The signature must read them as one.
    commands = h.ScriptedCommands(
        outputs=[(1, h.PYTEST_FAILURE), (1, h.PYTEST_FAILURE_AGAIN)]
    )
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
    runtime = h.build_runtime(
        h.build_loaded(root, verify=h.pytest_spec()), tools=tools, git=git
    )
    commands = h.ScriptedCommands(outputs=[(1, h.PYTEST_FAILURE), (0, "2 passed")])
    conversation = Conversation(model=h.ScriptedModel(edits(4)), command=commands)  # type: ignore[arg-type]

    events = await h.collect(conversation.run_prompt(runtime, "fix widget"))

    report = conversation.last_repair
    assert report is not None and report.verdict is RepairVerdict.FIXED
    assert [e for e in events if isinstance(e, Error)] == []


async def test_a_green_first_pass_runs_no_repair_turn(tmp_path: Path) -> None:
    root, git, tools = verify_setup(tmp_path)
    runtime = h.build_runtime(
        h.build_loaded(root, verify=h.pytest_spec()), tools=tools, git=git
    )
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
    runtime = h.build_runtime(
        h.build_loaded(root, verify=h.pytest_spec()), tools=tools, git=git
    )
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
