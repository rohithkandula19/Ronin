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
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
import stream_harness as h

from ronin.cli.approve import Handoff, PromptAsker
from ronin.cli.main import (
    EXIT_USAGE,
    WIZARD_QUESTION,
    Command,
    ExportFormat,
    Options,
    SessionsOptions,
    Streams,
    Usage,
    _app_session,
    _asker,
    _wants_modal,
    dispatch,
    main,
    parse,
)
from ronin.cli.sdk import Agent
from ronin.cli.status import current_branch
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
from ronin.persistence.index import SessionIndex
from ronin.persistence.transcript import Transcript
from ronin.ui.app import textual_available
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
# eval / duel argv
# --------------------------------------------------------------------------- #


def test_eval_collects_every_selection_flag() -> None:
    parsed = options(
        [
            "eval",
            "--model",
            "qwen-local",
            "--parallel",
            "8",
            "--category",
            "single-file",
            "--category",
            "multi-file",
            "--tag",
            "quick",
            "--task",
            "single-file/x",
            "--limit",
            "12",
            "--json",
            "r.json",
            "--markdown",
            "r.md",
            "--keep-workspaces",
            "--record",
            "--allow-network",
        ]
    )
    assert parsed.command is Command.EVAL
    bench = parsed.bench
    assert bench is not None
    assert bench.models == ("qwen-local",)
    assert bench.parallel == 8
    assert bench.categories == frozenset({"single-file", "multi-file"})
    assert bench.tags == frozenset({"quick"})
    assert bench.ids == frozenset({"single-file/x"})
    assert bench.limit == 12
    assert bench.json_out == Path("r.json")
    assert bench.markdown_out == Path("r.md")
    assert bench.keep_workspaces and bench.record and bench.allow_network


def test_the_suite_default_is_left_unresolved_by_parsing() -> None:
    """``tests/evals`` is relative to the workspace root, which parsing has not found.

    Resolving it here would mean either touching the filesystem in a pure function or
    baking in a path that is wrong whenever ``--cwd`` was passed.
    """
    parsed = options(["eval", "--dry-run"])
    assert parsed.suite_given is False
    explicit = options(["eval", "--dry-run", "--suite", "/somewhere/else"])
    assert explicit.suite_given is True
    assert explicit.bench is not None
    assert explicit.bench.suite == Path("/somewhere/else")


def test_dry_run_is_the_one_way_to_omit_a_model() -> None:
    assert isinstance(parse(["eval"]), Usage)
    parsed = options(["eval", "--dry-run"])
    assert parsed.bench is not None
    assert parsed.bench.models == ()


def test_eval_refuses_two_models_and_names_duel() -> None:
    usage = parse(["eval", "--model", "a", "--model", "b"])
    assert isinstance(usage, Usage)
    assert "duel" in usage.message


def test_duel_requires_exactly_two_models() -> None:
    for argv in (
        ["duel", "--model", "a"],
        ["duel", "--model", "a", "--model", "b", "--model", "c"],
    ):
        usage = parse(argv)
        assert isinstance(usage, Usage), argv
        assert "two models" in usage.message
    parsed = options(["duel", "--model", "a", "--model", "b", "--seed", "9"])
    assert parsed.bench is not None
    assert parsed.bench.models == ("a", "b")
    assert parsed.bench.seed == 9


@pytest.mark.parametrize(
    "argv",
    [
        ["duel", "--dry-run"],
        ["duel", "--dry-run", "--model", "only-one"],
        ["eval", "--dry-run", "--model", "a", "--model", "b"],
    ],
    ids=["duel-no-models", "duel-one-model", "eval-two-models"],
)
def test_dry_run_relaxes_every_model_count_rule(argv: list[str]) -> None:
    """A flag whose purpose is "no provider needed" must not demand a provider.

    This shipped wrong for one commit: the argv layer enforced duel's two-model rule
    before looking at ``--dry-run``, while ``bench.run_duel_command`` short-circuits on
    dry-run first. Two copies of one rule, and only the CI smoke noticed.
    """
    parsed = parse(argv)
    assert not isinstance(parsed, Usage), parsed
    assert parsed.bench is not None
    assert parsed.bench.dry_run is True


def test_eval_takes_flags_not_a_prompt() -> None:
    """`ronin eval fix the bug` is a confusion worth catching at the boundary."""
    usage = parse(["eval", "--dry-run", "fix", "the", "bug"])
    assert isinstance(usage, Usage)
    assert "takes flags, not a prompt" in usage.message


def test_a_nonsense_parallel_or_limit_is_a_usage_error() -> None:
    assert isinstance(parse(["eval", "--dry-run", "--parallel", "0"]), Usage)
    assert isinstance(parse(["eval", "--dry-run", "--limit", "0"]), Usage)


def test_harvest_collects_its_flags() -> None:
    parsed = options(
        [
            "harvest",
            "--tasks",
            "pool",
            "--out",
            "datasets",
            "--model",
            "qwen-local",
            "--parallel",
            "16",
            "--category",
            "single-file",
            "--container",
        ]
    )
    assert parsed.command is Command.HARVEST
    harvest = parsed.harvest
    assert harvest is not None
    assert harvest.tasks == Path("pool")
    assert harvest.out == Path("datasets")
    assert harvest.models == ("qwen-local",)
    assert harvest.parallel == 16
    assert harvest.categories == frozenset({"single-file"})
    assert harvest.container is True


def test_harvest_needs_tasks() -> None:
    usage = parse(["harvest", "--out", "datasets", "--dry-run"])
    assert isinstance(usage, Usage)
    assert "--tasks" in usage.message


def test_harvest_needs_out_unless_dry_run() -> None:
    assert isinstance(parse(["harvest", "--tasks", "pool"]), Usage)
    # --dry-run writes nothing, so it needs no --out.
    parsed = options(["harvest", "--tasks", "pool", "--dry-run"])
    assert parsed.harvest is not None
    assert parsed.harvest.dry_run is True


def test_harvest_runs_one_model() -> None:
    usage = parse(["harvest", "--tasks", "pool", "--out", "d", "--model", "a", "--model", "b"])
    assert isinstance(usage, Usage)
    assert "one model" in usage.message


def test_harvest_takes_flags_not_a_prompt() -> None:
    usage = parse(["harvest", "--tasks", "pool", "--out", "d", "fix", "it"])
    assert isinstance(usage, Usage)
    assert "takes flags, not a prompt" in usage.message


def test_a_run_carries_no_harvest_options() -> None:
    """``harvest`` is ``None`` off that command, so a stray read fails loudly."""
    assert options(["do a thing"]).harvest is None


def test_a_run_carries_no_bench_options() -> None:
    """``bench`` is ``None`` off these two commands, so a stray read fails loudly."""
    assert options(["hello"]).bench is None
    assert options(["doctor"]).bench is None


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
    return await dispatch(options(argv), streams=capture.streams(), environ={}, agent=agent)


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
    agent = agent_for(tmp_path, [h.call("read", {"file_path": "a.py"}), h.say("the answer")])

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
    agent = agent_for(tmp_path, [h.call("read", {"file_path": "a.py"}), h.say("just the answer")])

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

    code = await run(
        ["--no-wizard", "-c", "-p", "carry on", "--cwd", str(tmp_path)], agent, capture
    )

    assert code == 0
    assert "resumed 20240102-000000-bbbbbb" in capture.stderr


async def test_continue_with_nothing_recorded_says_so(tmp_path: Path) -> None:
    capture = Captured()
    agent = agent_for(tmp_path, [])

    code = await run(
        ["--no-wizard", "-c", "-p", "carry on", "--cwd", str(tmp_path)], agent, capture
    )

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


async def test_sessions_lists_without_needing_the_index_at_all(tmp_path: Path) -> None:
    """The command people run most often does not depend on the cache.

    ``record_a_session`` attaches no index, so this is the cold case: no
    ``index.sqlite3`` exists, and the plain listing still works because it reads the
    sidecars. That is what makes the index safe to have — a broken or absent database
    costs a user nothing on the path they use daily.
    """
    record_a_session(tmp_path, "20240101-000000-aaaaaa")
    capture = Captured()

    code = await dispatch(
        options(["sessions", "--cwd", str(tmp_path)]), streams=capture.streams(), environ={}
    )

    assert code == 0
    assert "20240101-000000-aaaaaa" in capture.stdout
    assert not (tmp_path / ".ronin" / "sessions" / "index.sqlite3").exists()


async def test_reindex_then_search_finds_a_session_by_its_prompt(tmp_path: Path) -> None:
    """The end-to-end path a user actually walks: sessions already on disk, no index
    yet, one ``--reindex``, then find the session by what they remember asking."""
    record_a_session(tmp_path, "20240101-000000-aaaaaa")

    build = Captured()
    assert (
        await dispatch(
            options(["sessions", "--reindex", "--cwd", str(tmp_path)]),
            streams=build.streams(),
            environ={},
        )
        == 0
    )
    assert "1 session(s) indexed" in build.stdout

    found = Captured()
    code = await dispatch(
        options(["sessions", "--search", "widget", "--cwd", str(tmp_path)]),
        streams=found.streams(),
        environ={},
    )
    assert code == 0
    assert "20240101-000000-aaaaaa" in found.stdout
    assert "widget" in found.stdout, "the snippet should show why it matched"


async def test_a_search_with_no_index_says_what_to_run(tmp_path: Path) -> None:
    """ "No match" and "nothing is indexed" are different facts, and conflating them
    sends someone looking for work they still have."""
    record_a_session(tmp_path, "20240101-000000-aaaaaa")
    capture = Captured()

    code = await dispatch(
        options(["sessions", "--search", "widget", "--cwd", str(tmp_path)]),
        streams=capture.streams(),
        environ={},
    )

    assert code == 0
    assert "--reindex" in capture.stdout


async def test_bare_words_after_sessions_are_a_search(tmp_path: Path) -> None:
    """``ronin sessions widget`` does the obvious thing, so nobody has to know the
    flag exists."""
    record_a_session(tmp_path, "20240101-000000-aaaaaa")
    await dispatch(
        options(["sessions", "--reindex", "--cwd", str(tmp_path)]),
        streams=Captured().streams(),
        environ={},
    )
    capture = Captured()

    code = await dispatch(
        options(["sessions", "widget", "--cwd", str(tmp_path)]),
        streams=capture.streams(),
        environ={},
    )

    assert code == 0
    assert "20240101-000000-aaaaaa" in capture.stdout


async def test_an_apostrophe_in_a_search_does_not_crash_the_command(tmp_path: Path) -> None:
    """The one that would have shipped broken: a raw query reaches fts5 as an
    expression, and ``don't`` raises ``unterminated string`` inside sqlite."""
    record_a_session(tmp_path, "20240101-000000-aaaaaa")
    await dispatch(
        options(["sessions", "--reindex", "--cwd", str(tmp_path)]),
        streams=Captured().streams(),
        environ={},
    )
    capture = Captured()

    code = await dispatch(
        options(["sessions", "--search", "don't panic", "--cwd", str(tmp_path)]),
        streams=capture.streams(),
        environ={},
    )

    assert code == 0
    assert "no match" in capture.stdout


async def test_sessions_by_cost_orders_by_the_number_the_filesystem_cannot_sort(
    tmp_path: Path,
) -> None:
    """``--by-cost`` is the reason the index exists at all."""
    record_a_session(tmp_path, "20240101-000000-cheap0")
    record_a_session(tmp_path, "20240101-000000-pricey")
    directory = tmp_path / ".ronin" / "sessions"
    index = SessionIndex.open(directory)
    index.rebuild(directory)
    index.record(
        replace(
            index.recent(cwd=str(tmp_path))[0], session_id="20240101-000000-pricey", cost_usd=9.5
        )
    )
    capture = Captured()

    code = await dispatch(
        options(["sessions", "--by-cost", "--cwd", str(tmp_path)]),
        streams=capture.streams(),
        environ={},
    )

    assert code == 0
    rows = [line for line in capture.stdout.splitlines() if line.strip()]
    assert rows[0].startswith("20240101-000000-pricey"), "the expensive session comes first"


def test_the_sessions_flags_parse_into_data(tmp_path: Path) -> None:
    """Parsing stays pure: the flags become a value, and nothing has touched a disk."""
    parsed = parse(["sessions", "--search", "a b", "--by-cost", "--reindex"])
    assert isinstance(parsed, Options)
    assert parsed.sessions is not None
    assert parsed.sessions.search == "a b"
    assert parsed.sessions.by_cost is True
    assert parsed.sessions.reindex is True

    bare = parse(["sessions"])
    assert isinstance(bare, Options)
    assert bare.sessions == SessionsOptions()


def test_an_explicit_search_flag_beats_bare_words_rather_than_joining_them() -> None:
    """Concatenating the two would search for a phrase the user never typed, and the
    empty result would read as a broken index."""
    parsed = parse(["sessions", "--search", "chosen", "ignored", "words"])
    assert isinstance(parsed, Options)
    assert parsed.sessions is not None
    assert parsed.sessions.search == "chosen"


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


# --------------------------------------------------------------------------- #
# The shipped console script
# --------------------------------------------------------------------------- #


def test_every_declared_entry_point_resolves_to_a_callable() -> None:
    """`ronin` and `ronin2` must both name something importable.

    A typo in either string is invisible until somebody installs the wheel — `uv run`
    and `python -m ronin` both work regardless, so the whole local development loop
    passes while the published artifact has a broken command. Resolved here the same
    way importlib.metadata resolves it at install time.
    """
    import tomllib
    from importlib import import_module

    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = config["project"]["scripts"]

    assert {"ronin", "ronin2"} <= set(scripts), "both console scripts must stay declared"
    for name, target in scripts.items():
        module_path, _, attribute = target.partition(":")
        resolved = getattr(import_module(module_path), attribute)
        assert callable(resolved), name
        assert resolved is main, f"{name} must point at the same main() tested above"


def test_no_two_distributions_in_this_workspace_claim_one_command_name() -> None:
    """The invariant behind the `ronin` / `ronin1` / `ronin2` naming, as a gate.

    Console scripts are not namespaced. When two installed distributions declare the same
    name, whichever was installed second overwrites the other's launcher — silently, with
    no warning and no error, so a user who upgraded one of them for an unrelated reason
    finds a different agent behind the same word. That is why the root tree held off on
    `ronin` for as long as `packages/cli` declared it, and why v1's entry point was
    *renamed* to `ronin1` rather than left to collide.

    Checked across the whole workspace rather than between those two files, because the
    next collision will be a third package nobody thought about.
    """
    import tomllib

    root = Path(__file__).resolve().parents[2]
    members = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["uv"][
        "workspace"
    ]["members"]

    owners: dict[str, list[str]] = {}
    for manifest in [
        root / "pyproject.toml",
        *(root / member / "pyproject.toml" for member in members),
    ]:
        if not manifest.is_file():
            continue
        project = tomllib.loads(manifest.read_text(encoding="utf-8")).get("project", {})
        for name in project.get("scripts", {}):
            owners.setdefault(name, []).append(project.get("name", str(manifest)))

    clashes = {name: sources for name, sources in owners.items() if len(sources) > 1}
    assert not clashes, f"two distributions declare the same console script: {clashes}"


def test_the_console_script_returns_an_int_rather_than_exiting() -> None:
    """The wrapper turns the return value into the process status.

    If `main` called `sys.exit` instead, the exit codes (0 done, 1 error, 2 an approval
    was refused) would still work — but nothing in this file could assert on them
    without catching SystemExit, which is why the contract is a return value.
    """
    assert isinstance(main(["--help"]), int)


# --------------------------------------------------------------------------- #
# who answers an approval, and what the Textual view is actually wired to
# --------------------------------------------------------------------------- #


def test_a_terminal_gets_an_asker_and_a_pipe_does_not() -> None:
    """The choice that decides whether anyone *can* approve anything.

    A pipe gets ``None``, which means ``UnattendedAsker`` — every gated call refused. That
    is not a limitation to work around: an approval prompt written to a stream nobody is
    reading is an approval nobody gave.
    """
    piped = Captured()
    assert _asker(None, piped.streams()) is None

    terminal = Captured(isatty=True)
    assert isinstance(_asker(None, terminal.streams()), PromptAsker)

    handoff = Handoff()
    assert _asker(handoff, terminal.streams()) is handoff, "the modal wins over the prompt"


@pytest.mark.parametrize(
    ("argv", "isatty", "expected"),
    [
        (["fix it"], True, True),
        (["fix it"], False, False),
        (["--no-tui", "fix it"], True, False),
        ([], True, False),
    ],
    ids=["tty-with-prompt", "piped", "tui-disabled", "no-prompt"],
)
def test_the_modal_is_offered_exactly_when_the_textual_view_will_run(
    argv: Sequence[str], isatty: bool, expected: bool
) -> None:
    """``_wants_modal`` and ``_interactive`` must agree, or a run shows a modal nobody
    collects an answer from — or attaches an answer path to a session that never asks.
    The Textual condition is checked here against the same inputs the view uses."""
    capture = Captured(isatty=isatty)
    wants = _wants_modal(options(argv), capture.streams())
    assert wants == (expected and textual_available())


def test_the_textual_session_is_wired_to_the_live_policy(tmp_path: Path) -> None:
    """The defect this change fixes, asserted at the wiring rather than through a terminal.

    Every callback below was ``None`` in the shipped CLI: the app accepted them and this
    call site never passed them, so ``esc`` and ``shift+tab`` moved the status line and
    nothing else. Each assertion is "this key now reaches the thing it names".
    """
    handoff = Handoff()
    agent = agent_for(tmp_path, [])
    session = _app_session(options(["fix it"]), agent, handoff)
    policy = agent.runtime.policy

    assert session.on_interrupt is not None
    assert not policy.cancelled()
    session.on_interrupt()
    assert policy.cancelled(), "esc did not reach the policy engine"

    assert session.on_mode_change is not None
    session.on_mode_change(Mode.PLAN)
    assert policy.mode is Mode.PLAN, "shift+tab did not reach the policy engine"

    assert session.on_attach is not None, "nothing could ask the human"
    assert session.on_status is not None, "the status line had no source for context %"
    assert 0.0 <= session.on_status() <= 1.0


def test_the_textual_session_reports_the_model_and_branch_it_is_running_under(
    tmp_path: Path,
) -> None:
    """Both rendered as their empty defaults before this change, because nothing supplied
    them — a status bar that said ``(no branch)`` inside a git repository."""
    session = _app_session(options(["fix it"]), agent_for(tmp_path, []), None)

    assert session.model, "the status line had no model name"
    assert session.cwd == str(tmp_path)
    # The workspace here is a bare temp directory, so there is genuinely no branch; the
    # claim is that the value comes from the workspace rather than from a placeholder.
    assert session.branch == current_branch(tmp_path)


def test_without_a_handoff_the_textual_session_cannot_approve_anything(tmp_path: Path) -> None:
    """A run that fell back from Textual, or an injected agent whose consent path somebody
    else chose, must not be handed an answer path this function invented."""
    session = _app_session(options(["fix it"]), agent_for(tmp_path, []), None)
    assert session.on_attach is None
