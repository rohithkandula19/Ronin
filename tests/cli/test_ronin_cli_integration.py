"""Seven subsystems wired together, with a fake only at the provider seam.

``load_workspace`` → ``build_runtime`` → ``run_prompt`` → ``run_headless``, over a
real workspace on disk: real settings layers, real memory, real repo map, real
policy engine, real gated registry, real tools, real checkpoint store, real
transcript, real headless JSON. The only double is the thing at the bottom of the
router — a scripted ``providers.base.ModelClient`` — because everything above it
(the retry wrapper, ``LoopClient``, the stable-prefix assembly) is code whose joins
this file exists to check.

The 200-turn recall test lives here too, for the same reason: it is a *session*, not
a unit.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import stream_harness as h

from ronin.cli.stream import VERIFY_STEP, Conversation, run_prompt
from ronin.cli.wire import build_runtime, load_workspace
from ronin.core.protocols import FinalMessage, ModelChunk, TextChunk
from ronin.core.types import (
    Message,
    Mode,
    Role,
    Text,
    ToolEnd,
    ToolUse,
    unpaired_tool_uses,
)
from ronin.persistence.resume import resume_session
from ronin.persistence.transcript import read_events
from ronin.ui.headless import OutputFormat, run_headless

RONIN_MD = """\
# widget

## Commands
- test: `pytest -q`

## Conventions
- every public function has a docstring
"""

WIDGET = "def widget(x):\n    return round(x, 2)\n"


def workspace(tmp_path: Path) -> Path:
    """A small but real project: memory, a source tree, a declared test command."""
    (tmp_path / "RONIN.md").write_text(RONIN_MD, encoding="utf-8", newline="\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget.py").write_text(WIDGET, encoding="utf-8", newline="\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_widget.py").write_text(
        "def test_widget():\n    assert True\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "widget"\nversion = "0.1.0"\n\n'
        '[dependency-groups]\ndev = ["pytest>=8"]\n',
        encoding="utf-8",
        newline="\n",
    )
    return tmp_path


# --------------------------------------------------------------------------- #
# The whole stack, one prompt
# --------------------------------------------------------------------------- #


async def test_the_whole_stack_answers_one_prompt_end_to_end(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    router, provider = h.scripted_router(
        [
            h.provider_calls("read", {"path": "src/widget.py"}),
            h.provider_says("it rounds to two places."),
        ]
    )
    loaded = load_workspace(
        h.build_loaded(root).paths, flags={"mode": Mode.AUTO_EDIT.value}, environ={}
    )
    assert loaded.memory.files, "RONIN.md was not loaded"
    assert loaded.verify.test is not None, "pytest was not detected from pyproject.toml"

    runtime = await build_runtime(loaded, router, session_id="itest", connect_mcp=False)
    out: list[str] = []
    err: list[str] = []
    try:
        result = await run_headless(
            run_prompt(runtime, "what does widget do?"),
            output_format=OutputFormat.STREAM_JSON,
            write=out.append,
            write_error=err.append,
            flush=lambda: None,
        )
    finally:
        await runtime.aclose()

    assert result.exit_code == 0, "".join(err)
    assert "two places" in result.text

    records = [json.loads(line) for line in "".join(out).splitlines() if line.strip()]
    kinds = [record["type"] for record in records]
    assert kinds[0] == "turn_start" and kinds[-1] == "result"
    assert "tool_start" in kinds and "tool_end" in kinds
    # The real read tool really read the real file, through the real gate.
    read_end = next(r for r in records if r["type"] == "tool_end" and r["name"] == "read")
    assert read_end["ok"] is True
    assert "round" in read_end["content"]

    # RONIN.md and the repo map reached the request's system prompt, which is what
    # makes ``pinned_prefix_tokens`` a real number rather than a hypothesis.
    assert provider.requests
    assert "every public function has a docstring" in provider.requests[0].system
    assert loaded.pinned_prefix_tokens() > 0

    # And the whole session is on disk, replayable.
    replayed = resume_session(loaded.paths.sessions_dir, "itest")
    assert replayed.skipped == ()
    assert replayed.state.pairing_errors == ()
    assert any("two places" in message.text for message in replayed.state.messages)


@pytest.mark.skipif(shutil.which("git") is None, reason="the checkpoint store needs git")
async def test_a_mutating_turn_checkpoints_and_verifies_through_the_real_stack(
    tmp_path: Path,
) -> None:
    root = workspace(tmp_path)
    import subprocess

    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    router, _provider = h.scripted_router(
        [
            h.provider_calls(
                "write", {"path": "src/new_helper.py", "content": "def helper():\n    return 1\n"}
            ),
            h.provider_says("added the helper."),
        ]
    )
    loaded = load_workspace(
        h.build_loaded(root).paths, flags={"mode": Mode.AUTO_EDIT.value}, environ={}
    )
    runtime = await build_runtime(
        loaded,
        router,
        session_id="mutating",
        connect_mcp=False,
        asker=h.ApprovingAsker(),
    )
    commands = h.ScriptedCommands(outputs=[(0, "1 passed in 0.02s")])
    conversation = Conversation(command=commands)
    try:
        events = await h.collect(conversation.run_prompt(runtime, "add a helper"))
    finally:
        await runtime.aclose()

    assert (root / "src" / "new_helper.py").is_file()
    # A real shadow-git checkpoint, in .ronin/checkpoints, and the user's own index
    # untouched: `git status` still reports the new file as untracked.
    assert conversation.checkpoints, conversation.notes
    assert (root / ".ronin" / "checkpoints" / "HEAD").is_file()
    status = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "?? src/new_helper.py" in status.stdout
    # Nothing was staged in the user's own index. The shadow repo has its own.
    assert not [line for line in status.stdout.splitlines() if not line.startswith("??")]

    # The changed path came from the diff, not from the model's account of itself.
    verify = [e for e in events if isinstance(e, ToolEnd) and e.name == VERIFY_STEP]
    assert verify and verify[0].result.ok
    assert commands.runs == 1
    assert any("src/new_helper.py" in " ".join(argv) for argv in commands.argv_log)


# --------------------------------------------------------------------------- #
# The 200-turn recall test
# --------------------------------------------------------------------------- #

TURNS = 200
RECALL_TURN = 3
BODY_CHARS = 400


def marker(turn: int) -> str:
    return f"WIDGET-{turn:03d}"


def path_for(turn: int) -> str:
    return f"src/mod{turn:03d}.py"


async def five_section_summary(prompt: str) -> str:
    """A summarizer that returns the five sections compaction requires.

    Scripted rather than modelled: the point of the test is retention, and a
    summarizer that omitted a section would make it a test of ``missing_sections``.
    """
    del prompt
    return (
        "## Goal\nedit two hundred modules\n\n"
        "## Files touched\n- many\n\n"
        "## Learned\nthe tree is wide\n\n"
        "## Failed\nnothing\n\n"
        "## Remaining\nkeep going\n"
    )


def two_hundred_turn_script(turn: int) -> tuple[ModelChunk, ...]:
    """Alternating responses: edit a distinct file, then say what was done.

    Each ``run_prompt`` drives two model calls — the tool call, then the answer — so
    the parity of the call index is what decides which one this is.
    """
    if turn % 2 == 0:
        index = turn // 2
        return (
            FinalMessage(
                message=Message(
                    role=Role.ASSISTANT,
                    content_blocks=(
                        ToolUse(
                            id=f"u{index:03d}",
                            name="edit",
                            arguments={"file_path": path_for(index)},
                        ),
                    ),
                ),
                input_tokens=12,
                output_tokens=6,
            ),
        )
    index = turn // 2
    return (
        TextChunk(f"edited {path_for(index)}"),
        FinalMessage(
            message=Message(
                role=Role.ASSISTANT,
                content_blocks=(Text(f"edited {path_for(index)}"),),
            ),
            input_tokens=12,
            output_tokens=6,
        ),
    )


async def test_two_hundred_turns_of_compaction_still_recall_turn_threes_file(
    tmp_path: Path,
) -> None:
    """The property ``context.compaction`` exists for, driven through ``run_prompt``.

    Two hundred real turns, each editing a distinct path, with a context window small
    enough that compaction fires again and again. After all of it, the transcript the
    model is handed must still contain — in full, not in summary — the tool result for
    the file edited in turn 3. "The most recent tool result per unique file path
    survives" is what makes "what file did we edit in turn 3" answerable, and a
    summary of a diff is not a diff.
    """
    root = tmp_path
    loaded = h.build_loaded(root, repo_map_tokens=300)
    tools = h.ScriptedTools(
        [
            h.FakeTool(
                name="edit",
                danger=h.DangerLevel.READ_ONLY,  # no checkpoints: this is a context test
                result=h.ToolResult(ok=True, content="unused"),
                handler=lambda arguments: h.ToolResult(
                    ok=True,
                    content=(
                        f"{marker(int(str(arguments['file_path'])[7:10]))} "
                        f"applied to {arguments['file_path']}\n" + "x" * BODY_CHARS
                    ),
                ),
            )
        ]
    )
    runtime = h.build_runtime(loaded, tools=tools, context_window=6_000)
    model = h.CyclingModel(two_hundred_turn_script)
    conversation = Conversation(model=model)

    for turn in range(TURNS):
        await h.collect(
            conversation.run_prompt(
                runtime,
                f"edit {path_for(turn)}",
                summarizer=five_section_summary,
                verify=False,
            )
        )
        # The invariant that makes the rest of it possible: never an unpaired call.
        assert unpaired_tool_uses(conversation.messages) == ()

    assert conversation.compactions > 1, "compaction never fired more than once"
    # The question, asked of the thing that would have to answer it: the messages
    # the model was handed on the last turn.
    last = model.calls[-1].rendered()
    assert marker(RECALL_TURN) in last, (
        f"the turn-{RECALL_TURN} tool result did not survive "
        f"{conversation.compactions} compactions"
    )
    assert path_for(RECALL_TURN) in last
    # In full, not summarized: the whole padded body is still there.
    assert "x" * BODY_CHARS in last
    # And the most recent turns are there too, so recall is not bought by pinning
    # only the beginning.
    assert marker(TURNS - 1) in last


async def test_the_two_hundred_turn_session_says_it_is_over_its_own_trigger(
    tmp_path: Path,
) -> None:
    """The honest cost of unbounded retention, reported rather than hidden.

    Two hundred distinct paths retained in full is bigger than the window, and
    ``CompactionResult.still_over_trigger`` is the signal that compacting again
    would change nothing. A session that kept quiet about that would spin.
    """
    loaded = h.build_loaded(tmp_path, repo_map_tokens=300)
    tools = h.ScriptedTools(
        [
            h.FakeTool(
                name="edit",
                handler=lambda arguments: h.ToolResult(
                    ok=True, content=f"applied to {arguments['file_path']}\n" + "y" * 600
                ),
            )
        ]
    )
    runtime = h.build_runtime(loaded, tools=tools, context_window=4_000)
    conversation = Conversation(model=h.CyclingModel(two_hundred_turn_script))

    for turn in range(40):
        await h.collect(
            conversation.run_prompt(
                runtime, f"edit {path_for(turn)}", summarizer=five_section_summary, verify=False
            )
        )

    result = conversation.last_compaction
    assert result is not None and result.compacted
    assert result.still_over_trigger
    assert any("still at or above the compaction trigger" in n for n in conversation.notes)


# --------------------------------------------------------------------------- #
# Recording and replay of a middleware step
# --------------------------------------------------------------------------- #


async def test_the_middleware_steps_are_recorded_and_replay_cleanly(tmp_path: Path) -> None:
    root = h.git_repo(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "widget.py").write_text(WIDGET, encoding="utf-8")
    from ronin.persistence.transcript import Transcript

    transcript = Transcript.open(root / ".ronin" / "sessions", "steps", cwd=str(root))
    git = h.FakeGit(diff=h.DIFF_ONE_FILE)
    runtime = h.build_runtime(
        h.build_loaded(root, verify=h.pytest_spec()),
        tools=h.ScriptedTools([h.writer(root)]),
        git=git,
        transcript=transcript,
    )
    commands = h.ScriptedCommands(outputs=[(0, "1 passed")])
    model = h.ScriptedModel(
        [h.call("edit", {"file_path": "src/widget.py", "content": "z"}), h.say("done")]
    )

    events = await h.collect(
        Conversation(model=model, command=commands).run_prompt(runtime, "edit it")
    )
    transcript.close()

    recorded = read_events(transcript.path)
    assert len(recorded.events) == len(events)
    names = {e.name for e in recorded.events if isinstance(e, ToolEnd)}
    assert {"checkpoint", "edit", "verify"} <= names
    replayed = resume_session(root / ".ronin" / "sessions", "steps")
    assert replayed.state.pairing_errors == ()
