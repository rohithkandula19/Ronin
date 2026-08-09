"""argv in, exit code out — tested with real argv and no subprocess.

Two halves, matching the module: :func:`ronin.cli.main.parse` is pure and is
asserted directly, and :func:`ronin.cli.main.dispatch` is driven with an injected
:class:`~ronin.cli.sdk.Agent` and injected streams, so all three exit codes — 0
done, 1 error, 2 needs approval — are reachable from a test rather than only from a
shell.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import stream_harness as h

from ronin.cli.main import (
    EXIT_USAGE,
    WIZARD_QUESTION,
    Command,
    ExportFormat,
    Options,
    Streams,
    Usage,
    dispatch,
    main,
    parse,
)
from ronin.cli.sdk import Agent
from ronin.cli.stream import Conversation
from ronin.core.protocols import TextChunk
from ronin.core.types import (
    AgentState,
    DangerLevel,
    Message,
    Mode,
    Role,
    Text,
    TextDelta,
    ToolResult,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.persistence.transcript import Transcript
from ronin.ui.headless import OutputFormat


@dataclass(slots=True)
class Captured:
    """Injected streams that record instead of writing. Mutable is the point."""

    out: list[str] = field(default_factory=list)
    err: list[str] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    isatty: bool = False

    def streams(self) -> Streams:
        def ask(question: str) -> str:
            self.questions.append(question)
            return self.answers.pop(0) if self.answers else ""

        return Streams(
            out=self.out.append,
            err=self.err.append,
            ask=ask,
            flush=lambda: None,
            isatty=self.isatty,
        )

    @property
    def stdout(self) -> str:
        return "".join(self.out)

    @property
    def stderr(self) -> str:
        return "".join(self.err)


def options(argv: Sequence[str]) -> Options:
    parsed = parse(argv)
    assert isinstance(parsed, Options), parsed
    return parsed


def usage(argv: Sequence[str]) -> Usage:
    parsed = parse(argv)
    assert isinstance(parsed, Usage), parsed
    return parsed


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #


def test_a_bare_prompt_is_an_interactive_run() -> None:
    parsed = options(["fix", "the", "flaky", "test"])

    assert parsed.command is Command.RUN
    assert parsed.prompt == "fix the flaky test"
    assert parsed.headless is False


def test_print_is_headless_and_defaults_to_text() -> None:
    parsed = options(["-p", "summarize the repo"])

    assert parsed.headless is True
    assert parsed.prompt == "summarize the repo"
    assert parsed.output_format is OutputFormat.TEXT


def test_an_explicit_output_format_forces_headless() -> None:
    # Asking for machine-readable output is a statement that something is parsing
    # it, which cannot be true with a terminal app in the way.
    parsed = options(["--output-format", "stream-json", "do", "a", "thing"])

    assert parsed.headless is True
    assert parsed.output_format is OutputFormat.STREAM_JSON


def test_modes_and_switches_become_settings_flags() -> None:
    parsed = options(["--mode", "plan", "--yolo", "--sandbox", "-p", "look around"])

    assert parsed.mode is Mode.PLAN
    assert parsed.flags == {"mode": "plan", "yolo": True, "sandbox": True}


def test_a_command_line_with_no_switches_overrides_no_setting() -> None:
    # The flags layer is the highest-precedence settings layer, so a default
    # written in here would silently beat a project's settings.json.
    assert options(["-p", "x"]).flags == {}


def test_continue_is_resume_with_no_id() -> None:
    assert options(["--continue"]).resume == ""
    assert options(["-c"]).resume == ""
    assert options(["--resume", "20240101-000000-abc"]).resume == "20240101-000000-abc"
    assert options(["--resume"]).resume == ""
    assert options([]).resume is None


def test_continue_and_a_named_resume_are_a_usage_error() -> None:
    failed = usage(["--continue", "--resume", "some-id"])

    assert failed.exit_code == EXIT_USAGE
    assert "different sessions" in failed.message


def test_print_with_no_prompt_is_a_usage_error() -> None:
    failed = usage(["--output-format", "json"])

    assert failed.exit_code == EXIT_USAGE
    assert "needs a prompt" in failed.message


def test_an_unknown_flag_is_a_usage_error_not_a_crash() -> None:
    failed = usage(["--frobnicate"])

    assert failed.exit_code == EXIT_USAGE
    assert "unrecognized" in failed.message
    assert failed.to_stdout is False


def test_help_is_a_zero_exit_on_stdout() -> None:
    failed = usage(["--help"])

    assert failed.exit_code == 0
    assert failed.to_stdout is True
    assert "--output-format" in failed.message
    assert "doctor" in failed.message and "export" in failed.message


def test_ceilings_only_produce_a_budget_when_one_was_asked_for() -> None:
    assert options(["-p", "x"]).budget is None
    budget = options(["-p", "x", "--max-tokens", "500", "--max-usd", "1.5"]).budget
    assert budget is not None
    assert budget.max_tokens == 500
    assert budget.max_usd == 1.5
    assert budget.max_wall_seconds is None


def test_subcommands_are_recognized_by_their_first_token() -> None:
    assert options(["doctor"]).command is Command.DOCTOR
    assert options(["sessions"]).command is Command.SESSIONS
    exported = options(["export", "some-id", "--format", "html", "-o", "out.html"])
    assert exported.command is Command.EXPORT
    assert exported.export_session == "some-id"
    assert exported.export_format is ExportFormat.HTML
    assert exported.export_out == Path("out.html")


def test_a_prompt_that_looks_like_a_subcommand_word_is_still_a_prompt() -> None:
    # Only the *first* token dispatches, so "explain the export path" is a prompt.
    assert options(["explain", "the", "export", "path"]).command is Command.RUN


# --------------------------------------------------------------------------- #
# dispatch — exit codes
# --------------------------------------------------------------------------- #


def agent_for(
    tmp_path: Path,
    responses: list[object],
    *,
    tools: h.ScriptedTools | None = None,
    approval: bool = False,
) -> Agent:
    from ronin.safety.policy import Decision

    loaded = h.build_loaded(tmp_path, mode=Mode.ASK if approval else Mode.AUTO_EDIT)
    runtime = h.build_runtime(
        loaded,
        tools=tools if tools is not None else h.ScriptedTools([h.reader()]),
        default_decision=Decision.ASK if approval else Decision.ALLOW,
    )
    model = h.ScriptedModel(responses)  # type: ignore[arg-type]
    return Agent(runtime, conversation=Conversation(model=model))


async def run(argv: Sequence[str], agent: Agent, capture: Captured) -> int:
    return await dispatch(
        options(argv), streams=capture.streams(), environ={}, agent=agent
    )


async def test_a_finished_turn_exits_zero(tmp_path: Path) -> None:
    capture = Captured()
    agent = agent_for(tmp_path, [h.say("all done")])

    code = await run(["--no-wizard", "-p", "go", "--cwd", str(tmp_path)], agent, capture)

    assert code == 0
    assert capture.stdout == "all done\n"
    assert capture.stderr == ""


async def test_a_stream_that_errors_exits_one(tmp_path: Path) -> None:
    # A model stream with no FinalMessage is the loop's protocol error: an Error
    # event and a TurnEnd in state ERROR.
    capture = Captured()
    agent = agent_for(tmp_path, [(TextChunk("half a sentence"),)])

    code = await run(["--no-wizard", "-p", "go", "--cwd", str(tmp_path)], agent, capture)

    assert code == 1
    assert "ERROR [protocol]" in capture.stderr


async def test_a_denied_approval_exits_two(tmp_path: Path) -> None:
    capture = Captured()
    gated = h.ScriptedTools(
        [
            h.FakeTool(
                name="deploy",
                danger=DangerLevel.DESTRUCTIVE,
                requires_approval=True,
                result=ToolResult(ok=True, content="deployed"),
            )
        ]
    )
    agent = agent_for(
        tmp_path,
        [h.call("deploy", {"target": "prod"}), h.say("I was refused")],
        tools=gated,
        approval=True,
    )

    code = await run(["--no-wizard", "-p", "deploy it", "--cwd", str(tmp_path)], agent, capture)

    assert code == 2
    assert "DENIED (non-interactive, no human attached)" in capture.stderr


# --------------------------------------------------------------------------- #
# dispatch — output formats
# --------------------------------------------------------------------------- #


async def test_stream_json_emits_one_valid_json_object_per_line(tmp_path: Path) -> None:
    capture = Captured()
    agent = agent_for(
        tmp_path, [h.call("read", {"file_path": "a.py"}), h.say("the answer")]
    )

    code = await run(
        ["--no-wizard", "--output-format", "stream-json", "-p", "go", "--cwd", str(tmp_path)],
        agent,
        capture,
    )

    assert code == 0
    lines = [line for line in capture.stdout.splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]
    assert all(isinstance(record, dict) and "type" in record for record in records)
    kinds = [record["type"] for record in records]
    assert kinds[0] == "turn_start"
    assert kinds[-1] == "result"
    assert "tool_start" in kinds and "tool_end" in kinds
    assert records[-1]["exit_code"] == 0
    assert records[-1]["text"] == "the answer"


async def test_text_output_carries_the_answer_and_nothing_else(tmp_path: Path) -> None:
    capture = Captured()
    agent = agent_for(
        tmp_path, [h.call("read", {"file_path": "a.py"}), h.say("just the answer")]
    )

    await run(["--no-wizard", "-p", "go", "--cwd", str(tmp_path)], agent, capture)

    # A consumer piping stdout gets the answer, not the tool trace.
    assert capture.stdout == "just the answer\n"


# --------------------------------------------------------------------------- #
# dispatch — resume, export, sessions, doctor, wizard
# --------------------------------------------------------------------------- #


def record_a_session(root: Path, session_id: str = "20240101-000000-aaaaaa") -> Transcript:
    """A real recorded session in ``root``, written by the real transcript writer."""
    directory = root / ".ronin" / "sessions"
    transcript = Transcript.open(directory, session_id, cwd=str(root), model="stub-1")
    state = AgentState(
        messages=(
            Message(role=Role.USER, content_blocks=(Text("what is in widget.py?"),)),
            Message(role=Role.ASSISTANT, content_blocks=(Text("a rounding bug"),)),
        )
    )
    transcript.append(TurnStart(turn_index=0))
    transcript.append(TextDelta(text="a rounding bug"))
    transcript.append(
        TurnEnd(
            turn_index=0,
            state=TurnState.DONE,
            stop_reason="no_tool_calls",
            agent_state=state,
        )
    )
    transcript.close()
    return transcript


async def test_resume_restores_a_recorded_session(tmp_path: Path) -> None:
    recorded = record_a_session(tmp_path)
    capture = Captured()
    agent = agent_for(tmp_path, [h.say("still a rounding bug")])

    code = await run(
        [
            "--no-wizard",
            "--resume",
            recorded.meta.session_id,
            "-p",
            "and now?",
            "--cwd",
            str(tmp_path),
        ],
        agent,
        capture,
    )

    assert code == 0
    assert f"resumed {recorded.meta.session_id}" in capture.stderr
    # The replayed messages reached the model, not just the agent.
    seen = agent.conversation.messages
    assert any("what is in widget.py?" in message.text for message in seen)
    assert any("a rounding bug" in message.text for message in seen)


async def test_continue_picks_the_newest_session_in_this_directory(tmp_path: Path) -> None:
    record_a_session(tmp_path, "20240101-000000-aaaaaa")
    record_a_session(tmp_path, "20240102-000000-bbbbbb")
    capture = Captured()
    agent = agent_for(tmp_path, [h.say("continued")])

    code = await run(["--no-wizard", "-c", "-p", "carry on", "--cwd", str(tmp_path)], agent, capture)

    assert code == 0
    assert "resumed 20240102-000000-bbbbbb" in capture.stderr


async def test_continue_with_nothing_recorded_says_so(tmp_path: Path) -> None:
    capture = Captured()
    agent = agent_for(tmp_path, [])

    code = await run(["--no-wizard", "-c", "-p", "carry on", "--cwd", str(tmp_path)], agent, capture)

    assert code == EXIT_USAGE
    assert "no recorded session" in capture.stderr
    assert "ronin sessions" in capture.stderr


async def test_export_writes_markdown_for_a_recorded_session(tmp_path: Path) -> None:
    recorded = record_a_session(tmp_path)
    capture = Captured()

    code = await dispatch(
        options(["export", recorded.meta.session_id, "--cwd", str(tmp_path)]),
        streams=capture.streams(),
        environ={},
    )

    assert code == 0
    assert recorded.meta.session_id in capture.stdout
    assert "a rounding bug" in capture.stdout


async def test_export_to_a_file_writes_html_and_reports_the_path(tmp_path: Path) -> None:
    recorded = record_a_session(tmp_path)
    target = tmp_path / "session.html"
    capture = Captured()

    code = await dispatch(
        options(
            [
                "export",
                recorded.meta.session_id,
                "--format",
                "html",
                "-o",
                str(target),
                "--cwd",
                str(tmp_path),
            ]
        ),
        streams=capture.streams(),
        environ={},
    )

    assert code == 0
    assert f"wrote {target}" in capture.stdout
    body = target.read_bytes()
    assert body.startswith(b"<!doctype html>") or b"<html" in body
    assert b"\r\n" not in body


async def test_export_with_nothing_recorded_is_an_error_with_the_directory(
    tmp_path: Path,
) -> None:
    capture = Captured()

    code = await dispatch(
        options(["export", "--cwd", str(tmp_path)]), streams=capture.streams(), environ={}
    )

    assert code == 1
    assert "no sessions recorded" in capture.stderr


async def test_sessions_lists_what_is_on_disk(tmp_path: Path) -> None:
    record_a_session(tmp_path, "20240101-000000-aaaaaa")
    capture = Captured()

    code = await dispatch(
        options(["sessions", "--cwd", str(tmp_path)]), streams=capture.streams(), environ={}
    )

    assert code == 0
    assert "20240101-000000-aaaaaa" in capture.stdout


async def test_doctor_reports_the_workspace_without_starting_a_session(
    tmp_path: Path,
) -> None:
    (tmp_path / "RONIN.md").write_text("# rules\n\nbe careful\n", encoding="utf-8")
    capture = Captured()

    code = await dispatch(
        options(["doctor", "--cwd", str(tmp_path)]), streams=capture.streams(), environ={}
    )

    assert code in (0, 1)  # a scratch directory legitimately fails the git check
    assert "ronin doctor" in capture.stdout
    assert "paths" in capture.stdout and "checks" in capture.stdout


async def test_the_first_run_wizard_asks_before_writing_anything(tmp_path: Path) -> None:
    capture = Captured(answers=["y\n"])
    agent = agent_for(tmp_path, [h.say("ready")])

    await run(["-p", "hello", "--cwd", str(tmp_path)], agent, capture)

    assert capture.questions == [WIZARD_QUESTION]
    assert "first-run plan" in capture.stdout
    assert (tmp_path / "RONIN.md").is_file()
    assert (tmp_path / ".ronin").is_dir()


async def test_a_declined_wizard_writes_nothing_and_the_session_still_runs(
    tmp_path: Path,
) -> None:
    capture = Captured(answers=["n\n"])
    agent = agent_for(tmp_path, [h.say("ready anyway")])

    code = await run(["-p", "hello", "--cwd", str(tmp_path)], agent, capture)

    assert code == 0
    assert capture.questions == [WIZARD_QUESTION]
    assert not (tmp_path / "RONIN.md").exists()
    assert "nothing written" in capture.stdout


async def test_no_wizard_never_asks(tmp_path: Path) -> None:
    capture = Captured(answers=["y\n"])
    agent = agent_for(tmp_path, [h.say("ready")])

    await run(["--no-wizard", "-p", "hello", "--cwd", str(tmp_path)], agent, capture)

    assert capture.questions == []
    assert not (tmp_path / "RONIN.md").exists()


# --------------------------------------------------------------------------- #
# dispatch — the line session
# --------------------------------------------------------------------------- #


async def test_the_line_session_runs_a_turn_then_leaves_on_eof(tmp_path: Path) -> None:
    capture = Captured(answers=["", ""])
    agent = agent_for(tmp_path, [h.say("hello back")])

    code = await dispatch(
        options(["--no-wizard", "--cwd", str(tmp_path), "hello"]),
        streams=capture.streams(),
        environ={},
        agent=agent,
    )

    assert code == 0
    assert "hello back" in capture.stdout


async def test_the_line_session_answers_slash_help_without_a_model(tmp_path: Path) -> None:
    capture = Captured(answers=["/help\n", ""])
    agent = agent_for(tmp_path, [])

    code = await dispatch(
        options(["--no-wizard", "--cwd", str(tmp_path)]),
        streams=capture.streams(),
        environ={},
        agent=agent,
    )

    assert code == 0
    assert "/compact" in capture.stdout and "/undo" in capture.stdout


async def test_the_line_session_names_an_unknown_slash_command(tmp_path: Path) -> None:
    capture = Captured(answers=["/frobnicate\n", ""])
    agent = agent_for(tmp_path, [])

    await dispatch(
        options(["--no-wizard", "--cwd", str(tmp_path)]),
        streams=capture.streams(),
        environ={},
        agent=agent,
    )

    assert "frobnicate" in capture.stderr


async def test_exit_leaves_the_line_session(tmp_path: Path) -> None:
    capture = Captured(answers=["exit\n"])
    agent = agent_for(tmp_path, [])

    code = await dispatch(
        options(["--no-wizard", "--cwd", str(tmp_path)]),
        streams=capture.streams(),
        environ={},
        agent=agent,
    )

    assert code == 0


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def test_main_returns_the_usage_code_without_touching_the_process() -> None:
    # No SystemExit, no subprocess: `main` returns, which is what makes every path
    # above assertable.
    assert main(["--frobnicate"]) == EXIT_USAGE
    assert main(["--help"]) == 0


def test_main_with_no_model_configuration_returns_one(tmp_path: Path) -> None:
    assert main(["--no-wizard", "-p", "hello", "--cwd", str(tmp_path)]) == 1
