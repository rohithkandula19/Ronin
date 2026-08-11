"""``ronin`` — argv in, exit code out.

The shape here is the point: **parse, then act**, with nothing in between. Parsing
is :func:`parse`, a pure function from a sequence of strings to either
:class:`Options` or :class:`Usage`. Acting is :func:`dispatch`, which takes those
options plus an injected :class:`Streams` and returns an ``int``. :func:`main` is
four lines of glue. Nothing calls ``sys.exit``; nothing reads ``sys.argv``,
``os.environ`` or ``sys.stdout`` except the two defaults at the top of
:func:`main`. That is what makes every path below reachable from a test with real
argv and no subprocess — including the exit codes, which is the part most CLIs can
only test end-to-end.

**Exit codes come from ``ui.headless``, not from here.** ``0`` done, ``1`` error,
``2`` an approval was requested and denied. This module does not re-derive them; it
returns what :func:`ronin.ui.headless.run_headless` computed. A *usage* error exits
``1`` rather than argparse's conventional ``2``, because ``2`` already means
"needs approval" to every script that consumes this stream and having one number
mean two things is worse than deviating from argparse.

**``doctor`` and ``export`` are subcommands, not flags.** ``export`` takes a session
id, a format and an output path; as flags that is four options whose legal
combinations argparse cannot express, and ``--export --format html`` reads as though
it modified the run. Dispatch is by first token rather than through
``add_subparsers`` so that ``ronin "fix the test"`` — a bare positional prompt —
stays unambiguous.

**Plan mode is a registry, not a prompt.** ``--mode plan`` goes through
``agents.plan_mode`` via :func:`ronin.cli.stream.plan_runtime`, which subsets the
tool registry down to read-only and then *re-derives* that property from the specs.
A model told not to edit edits three turns later; a model with no ``write`` in its
registry cannot.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import TextIO

from ..core.types import (
    ApprovalRequest,
    Budget,
    Error,
    Event,
    Mode,
    TextDelta,
    ToolEnd,
    ToolStart,
)
from ..persistence import export as export_module
from ..persistence.index import SessionIndex, SessionIndexError
from ..persistence.resume import ResumedSession, resume_latest, resume_session
from ..persistence.transcript import list_sessions
from ..providers.router import Router
from ..providers.types import ProviderError
from ..telemetry import DISCLOSURE
from ..ui.app import TEXTUAL_MISSING, run_app, textual_available
from ..ui.app import Session as AppSession
from ..ui.commands import BUILTIN_REGISTRY, ParseError, is_command, render_help
from ..ui.commands import parse as parse_command
from ..ui.headless import (
    EXIT_ERROR,
    EXIT_OK,
    ApprovalTracker,
    OutputFormat,
    exit_code_for,
    run_headless,
)
from ..ui.reduce import ViewState, reduce_event, summarize_arguments, summarize_result
from ..ui.render import strip_controls
from .bench import BenchOptions, default_suite, run_duel_command, run_eval
from .doctor import run_doctor
from .sdk import Agent, load_router
from .spine import Paths
from .stream import DEFAULT_MAX_ITERATIONS
from .telemetry_cmd import TelemetryOptions, Verb
from .telemetry_cmd import run as run_telemetry
from .wire import load_workspace
from .wizard import apply_plan, plan_first_run

PROGRAM = "ronin"

#: Exit code for a malformed command line. Deliberately not argparse's 2 — see the
#: module docstring.
EXIT_USAGE = EXIT_ERROR

#: What the wizard is asked before it writes anything. A first run that creates
#: files in someone's repository without asking is a first run they do not trust.
WIZARD_QUESTION = "set up .ronin/ for this workspace? [Y/n] "

NO_TUI = (
    "the interactive Textual view needs a prompt to show. Falling back to the "
    "line-oriented session; type a request, or 'exit' to leave."
)

REPL_BANNER = "ronin — line session. type a request, /help for commands, 'exit' to leave."


class Command(StrEnum):
    """What the command line asked for. One value per top-level behaviour."""

    RUN = "run"
    DOCTOR = "doctor"
    EXPORT = "export"
    SESSIONS = "sessions"
    VERSION = "version"
    EVAL = "eval"
    DUEL = "duel"
    TELEMETRY = "telemetry"


class ExportFormat(StrEnum):
    MARKDOWN = "markdown"
    HTML = "html"


@dataclass(frozen=True, slots=True)
class Usage:
    """A command line that could not be turned into :class:`Options`.

    Carries the text *and* the code, so ``--help`` (text on stdout, exit 0) and a
    bad flag (text on stderr, exit 1) are one type rather than two code paths.
    """

    message: str
    exit_code: int = EXIT_USAGE

    @property
    def to_stdout(self) -> bool:
        return self.exit_code == EXIT_OK


@dataclass(frozen=True, slots=True)
class SessionsOptions:
    """What ``ronin sessions`` was asked to do. Built by :func:`parse`, which owns argv.

    Three questions over the same rows, so they are three flags on one verb rather
    than three verbs: list them, search them, or rebuild the index they come from.
    ``search`` holds a human's words, not fts5 syntax —
    :func:`ronin.persistence.index.fts_query` converts it, because a raw query with an
    apostrophe in it makes sqlite raise.
    """

    search: str = ""
    reindex: bool = False
    #: Order the rows come back in. ``cost`` is the reason the index exists: it is an
    #: ``ORDER BY`` the filesystem cannot answer at any price.
    by_cost: bool = False
    limit: int = 50


@dataclass(frozen=True, slots=True)
class Options:
    """Everything the command line decided, as data. No I/O, no defaults resolved
    against the filesystem — that happens in :func:`dispatch`, where it is visible."""

    command: Command = Command.RUN
    prompt: str = ""
    headless: bool = False
    output_format: OutputFormat = OutputFormat.TEXT
    mode: Mode | None = None
    yolo: bool = False
    sandbox: bool = False
    cwd: Path = field(default_factory=lambda: Path("."))
    #: ``None`` for no resume; ``""`` for ``--resume`` with no id (the latest here).
    resume: str | None = None
    verify: bool = True
    record: bool = True
    connect_mcp: bool = True
    wizard: bool = True
    tui: bool = True
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    budget: Budget | None = None
    export_session: str = ""
    export_format: ExportFormat = ExportFormat.MARKDOWN
    export_out: Path | None = None
    #: Present for ``eval``/``duel`` only. ``None`` everywhere else, so a code path
    #: that reads it without checking the command fails loudly instead of running an
    #: eval with default settings.
    bench: BenchOptions | None = None
    #: Whether ``--suite`` was passed. Distinguishes "the user chose this path" from
    #: "nobody said, so use the workspace default", which a bare ``Path()`` cannot.
    suite_given: bool = False
    #: Present for ``telemetry`` only, same rule as ``bench``.
    telemetry: TelemetryOptions | None = None
    #: Present for ``sessions`` only, same rule again.
    sessions: SessionsOptions | None = None

    @property
    def flags(self) -> dict[str, object]:
        """The settings-layer overrides this command line implies.

        Only keys the user actually passed appear, because the flags layer is the
        *highest* precedence layer: writing a default in here would silently beat a
        project's ``settings.json``.
        """
        overrides: dict[str, object] = {}
        if self.mode is not None:
            overrides["mode"] = self.mode.value
        if self.yolo:
            overrides["yolo"] = True
        if self.sandbox:
            overrides["sandbox"] = True
        return overrides


@dataclass(frozen=True, slots=True)
class Streams:
    """Where output goes and where input comes from, injected.

    Every one of these is a function rather than a file object so a test captures
    output by appending to a list, and so ``ask`` can be scripted — the first-run
    wizard prompts, and a prompt that cannot be faked is a prompt that forces a
    subprocess test.
    """

    out: Callable[[str], None]
    err: Callable[[str], None]
    ask: Callable[[str], str]
    flush: Callable[[], None]
    isatty: bool = False

    @classmethod
    def standard(cls, *, stdin: TextIO | None = None) -> Streams:
        """The real streams. The only place this module touches ``sys``."""
        source = sys.stdin if stdin is None else stdin

        def out(text: str) -> None:
            sys.stdout.write(text)

        def err(text: str) -> None:
            sys.stderr.write(text)

        def ask(question: str) -> str:
            sys.stdout.write(question)
            sys.stdout.flush()
            return source.readline()

        def flush() -> None:
            sys.stdout.flush()

        return cls(
            out=out,
            err=err,
            ask=ask,
            flush=flush,
            isatty=bool(getattr(source, "isatty", lambda: False)()),
        )


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


class _StopParsing(Exception):
    """argparse wanted to exit the process. Carried out as a value instead."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.usage = Usage(message=message, exit_code=exit_code)


class _Parser(argparse.ArgumentParser):
    """An ``ArgumentParser`` that never exits the process.

    ``error`` and ``exit`` are the only two ways argparse leaves, and both call
    ``sys.exit``. Overriding them is what lets :func:`parse` be a pure function and
    ``--help`` be testable without ``pytest.raises(SystemExit)``.
    """

    def error(self, message: str) -> None:  # type: ignore[override]  # never returns
        raise _StopParsing(f"{self.format_usage()}{PROGRAM}: error: {message}", EXIT_USAGE)

    def exit(self, status: int = 0, message: str | None = None) -> None:  # type: ignore[override]
        raise _StopParsing(message or "", status)


def build_parser() -> _Parser:
    """The whole argv surface, in one place so ``--help`` cannot drift from it."""
    parser = _Parser(
        prog=PROGRAM,
        # ``-h`` is added by hand so ``--help`` becomes a *value* (the formatted
        # text plus exit 0) instead of argparse printing to stdout and exiting the
        # process, which is untestable and unhookable.
        add_help=False,
        description="ronin — a masterless, terminal-native coding agent.",
        epilog=(
            "subcommands:\n"
            "  doctor                     report the workspace: paths, settings, "
            "notes\n"
            "  export [ID] [-o FILE]      write a session as markdown or html\n"
            "  sessions [TEXT]            list the sessions recorded in this "
            "directory,\n"
            "                             or search them (--search/--by-cost/"
            "--reindex)\n"
            "  eval [--dry-run]           run the task suite in tests/evals and "
            "report\n"
            "  duel --model A --model B   run the same tasks against two models\n"
            "  telemetry [status|on|off|show]\n"
            "                             opt-in aggregate task outcomes; off by "
            "default\n"
            "\n"
            "eval/duel flags: --suite PATH, --model NAME (repeat for duel), "
            "--parallel N,\n"
            "  --category C, --tag T, --task ID, --regression-gate, --limit N, "
            "--seed N,\n"
            "  --json FILE, --markdown FILE, --keep-workspaces, --record, "
            "--allow-network.\n"
            "  --dry-run lists what would run and opens no model, so it needs no key.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-h", "--help", action="store_true", help="show this help")
    parser.add_argument("words", nargs="*", help="the prompt, as bare words")
    parser.add_argument(
        "-p",
        "--print",
        dest="print_prompt",
        metavar="PROMPT",
        help="run PROMPT without a terminal and exit",
    )
    parser.add_argument(
        "--output-format",
        choices=[fmt.value for fmt in OutputFormat],
        default=None,
        help="headless output: text, json or stream-json",
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in Mode],
        default=None,
        help="permission mode; plan removes every mutating tool",
    )
    parser.add_argument(
        "--yolo", action="store_true", help="stop asking; the unconditional deny list still applies"
    )
    parser.add_argument(
        "--sandbox", action="store_true", help="run commands in a sandbox when one is available"
    )
    parser.add_argument(
        "--cwd", default=".", metavar="PATH", help="the directory to work in (default: .)"
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="",
        default=None,
        metavar="ID",
        help="resume a recorded session, newest in this directory if no ID is given",
    )
    parser.add_argument(
        "-c",
        "--continue",
        dest="continue_latest",
        action="store_true",
        help="resume the newest session in this directory",
    )
    parser.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="do not run the verify pass after a mutating turn",
    )
    parser.add_argument(
        "--no-record",
        dest="record",
        action="store_false",
        help="do not write a transcript for this session",
    )
    parser.add_argument(
        "--no-mcp",
        dest="connect_mcp",
        action="store_false",
        help="do not connect the configured MCP servers",
    )
    parser.add_argument(
        "--no-wizard", dest="wizard", action="store_false", help="skip the first-run setup prompt"
    )
    parser.add_argument(
        "--no-tui",
        dest="tui",
        action="store_false",
        help="use the line-oriented session even if Textual is present",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        metavar="N",
        help="iterations one turn may take",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=None, metavar="N", help="token ceiling for the session"
    )
    parser.add_argument(
        "--max-usd", type=float, default=None, metavar="USD", help="dollar ceiling for the session"
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        metavar="S",
        help="wall-clock ceiling for the session",
    )
    parser.add_argument(
        "--format",
        dest="export_format",
        choices=[fmt.value for fmt in ExportFormat],
        default=ExportFormat.MARKDOWN.value,
        help="export format (export only)",
    )
    parser.add_argument(
        "-o",
        "--out",
        dest="export_out",
        default=None,
        metavar="FILE",
        help="write the export here instead of stdout (export only)",
    )
    parser.add_argument(
        "--search",
        default="",
        metavar="TEXT",
        help="full-text search recorded prompts and answers (sessions only)",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="rebuild the sqlite session index from the transcripts (sessions only)",
    )
    parser.add_argument(
        "--by-cost",
        action="store_true",
        help="order sessions by cost instead of recency (sessions only)",
    )
    parser.add_argument("--version", action="store_true", help="print the version")

    # eval / duel. In this parser rather than a subparser so `--help` stays one page
    # and so a flag cannot be defined twice with two different meanings.
    parser.add_argument(
        "--suite",
        default=None,
        metavar="PATH",
        help="eval suite root (default: tests/evals under the workspace)",
    )
    parser.add_argument(
        "--model",
        dest="models",
        action="append",
        default=None,
        metavar="NAME",
        help="a model named in your router config; pass twice for duel",
    )
    parser.add_argument(
        "--parallel", type=int, default=1, metavar="N", help="tasks to run concurrently (eval/duel)"
    )
    parser.add_argument(
        "--category",
        dest="categories",
        action="append",
        default=None,
        metavar="C",
        help="only tasks in this category (repeatable)",
    )
    parser.add_argument(
        "--tag",
        dest="eval_tags",
        action="append",
        default=None,
        metavar="T",
        help="only tasks carrying this tag (repeatable)",
    )
    parser.add_argument(
        "--task",
        dest="task_ids",
        action="append",
        default=None,
        metavar="ID",
        help="only this task id (repeatable)",
    )
    parser.add_argument(
        "--regression-gate", action="store_true", help="only the tasks flagged regression_gate"
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="stop after N tasks")
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        metavar="N",
        help="duel seed; the same seed is used for both sides",
    )
    parser.add_argument(
        "--json",
        dest="eval_json",
        default=None,
        metavar="FILE",
        help="write the report as json here",
    )
    parser.add_argument(
        "--markdown",
        dest="eval_markdown",
        default=None,
        metavar="FILE",
        help="write the report as markdown here",
    )
    parser.add_argument(
        "--workspaces",
        dest="workspace_root",
        default=None,
        metavar="DIR",
        help="create task workspaces here instead of a temp dir",
    )
    parser.add_argument(
        "--keep-workspaces", action="store_true", help="do not delete task workspaces after the run"
    )
    parser.add_argument(
        "--record",
        dest="eval_record",
        action="store_true",
        help="write a transcript per task (what the harvest reads)",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="turn a git_sha task from skipped into a hard error",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would run; opens no model, needs no key"
    )
    return parser


def parse(argv: Sequence[str]) -> Options | Usage:
    """argv to options, or to the usage text that explains why not. Pure."""
    tokens = list(argv)
    command = Command.RUN
    if tokens and tokens[0] in {
        Command.DOCTOR.value,
        Command.EXPORT.value,
        Command.SESSIONS.value,
        Command.EVAL.value,
        Command.DUEL.value,
        Command.TELEMETRY.value,
    }:
        command = Command(tokens.pop(0))

    parser = build_parser()
    try:
        namespace = parser.parse_args(tokens)
    except _StopParsing as stop:
        return stop.usage

    if namespace.help:
        return Usage(parser.format_help(), exit_code=EXIT_OK)
    if namespace.version:
        return Options(command=Command.VERSION)

    words = " ".join(namespace.words).strip()
    if command is Command.EXPORT:
        return _export_options(namespace, words)
    if command in (Command.EVAL, Command.DUEL):
        return _bench_options(namespace, command, words)
    if command is Command.TELEMETRY:
        return _telemetry_options(namespace, words)
    if command is Command.SESSIONS:
        return _sessions_options(namespace, words)

    prompt = namespace.print_prompt if namespace.print_prompt is not None else words
    headless = namespace.print_prompt is not None
    if namespace.output_format is not None:
        chosen = OutputFormat(namespace.output_format)
        # An explicit --output-format is a statement that something is parsing this
        # output, which only makes sense without a terminal in the way.
        headless = True
    else:
        chosen = OutputFormat.TEXT
    if headless and not prompt.strip():
        return Usage(f"{PROGRAM}: error: --print needs a prompt, or pass one as bare words")

    resume = namespace.resume
    if namespace.continue_latest:
        if resume not in (None, ""):
            return Usage(
                f"{PROGRAM}: error: --continue and --resume <id> ask for different "
                "sessions; pass one of them"
            )
        resume = ""

    return Options(
        command=command,
        prompt=prompt,
        headless=headless,
        output_format=chosen,
        mode=Mode(namespace.mode) if namespace.mode else None,
        yolo=bool(namespace.yolo),
        sandbox=bool(namespace.sandbox),
        cwd=Path(namespace.cwd),
        resume=resume,
        verify=bool(namespace.verify),
        record=bool(namespace.record),
        connect_mcp=bool(namespace.connect_mcp),
        wizard=bool(namespace.wizard),
        tui=bool(namespace.tui),
        max_iterations=int(namespace.max_turns),
        budget=_budget(namespace),
    )


def _export_options(namespace: argparse.Namespace, words: str) -> Options | Usage:
    if namespace.print_prompt:
        return Usage(f"{PROGRAM}: error: export takes a session id, not --print")
    return Options(
        command=Command.EXPORT,
        cwd=Path(namespace.cwd),
        export_session=words,
        export_format=ExportFormat(namespace.export_format),
        export_out=Path(namespace.export_out) if namespace.export_out else None,
    )


def _sessions_options(namespace: argparse.Namespace, words: str) -> Options | Usage:
    """``sessions`` argv into :class:`SessionsOptions`.

    Bare words are taken as the search text, so ``ronin sessions pagination bug`` does
    the obvious thing without anyone having to know that ``--search`` exists. An
    explicit ``--search`` wins if both are given rather than being concatenated —
    silently searching for the two joined together is the kind of result that looks
    like the index is broken.
    """
    search = namespace.search or words
    return Options(
        command=Command.SESSIONS,
        cwd=Path(namespace.cwd),
        sessions=SessionsOptions(
            search=search,
            reindex=bool(namespace.reindex),
            by_cost=bool(namespace.by_cost),
        ),
    )


def _bench_options(namespace: argparse.Namespace, command: Command, words: str) -> Options | Usage:
    """``eval``/``duel`` argv into :class:`BenchOptions`, carried on :class:`Options`.

    ``--suite`` stays ``None`` here rather than being defaulted to ``tests/evals``:
    the default is relative to the *workspace root*, which is discovered in
    :func:`dispatch`, and resolving it in a pure parse function would mean either
    touching the filesystem here or hard-coding a path that is wrong when ``--cwd``
    was passed.
    """
    if words:
        return Usage(
            f"{PROGRAM}: error: {command.value} takes flags, not a prompt "
            f"(got {words!r}). Select tasks with --task/--category/--tag."
        )
    if namespace.print_prompt:
        return Usage(f"{PROGRAM}: error: {command.value} does not take --print")
    if namespace.parallel < 1:
        return Usage(f"{PROGRAM}: error: --parallel must be at least 1")
    if namespace.limit is not None and namespace.limit < 1:
        return Usage(f"{PROGRAM}: error: --limit must be at least 1")

    models = tuple(namespace.models or ())
    dry_run = bool(namespace.dry_run)
    # Model-count rules are checked here, where the message can name the other
    # command, rather than in the runner where the only honest thing left to say is
    # "wrong number of models".
    #
    # All of them are relaxed under --dry-run, which opens no model at all: a flag
    # whose purpose is "show me what would run without a provider" must not demand a
    # provider first. That inconsistency shipped for one commit and was caught by the
    # CI smoke rather than by a test, because the argv layer and `bench.py` each had
    # their own copy of the rule — so the argv tests below now cover the dry-run case
    # explicitly.
    if not dry_run:
        if command is Command.DUEL and len(models) != 2:
            return Usage(
                f"{PROGRAM}: error: duel compares two models — pass --model twice "
                f"(got {len(models)})"
            )
        if command is Command.EVAL and len(models) > 1:
            return Usage(
                f"{PROGRAM}: error: eval runs one model; use `{PROGRAM} duel` to "
                f"compare two (got {len(models)})"
            )
        if not models:
            return Usage(
                f"{PROGRAM}: error: {command.value} needs --model NAME, or --dry-run "
                "to see what would run without opening a model"
            )

    bench = BenchOptions(
        # Replaced in `dispatch` when None — see the docstring.
        suite=Path(namespace.suite) if namespace.suite else Path(),
        models=models,
        parallel=int(namespace.parallel),
        ids=frozenset(namespace.task_ids or ()),
        categories=frozenset(namespace.categories or ()),
        tags=frozenset(namespace.eval_tags or ()),
        regression_gate=bool(namespace.regression_gate),
        limit=namespace.limit,
        seed=int(namespace.seed),
        json_out=Path(namespace.eval_json) if namespace.eval_json else None,
        markdown_out=(Path(namespace.eval_markdown) if namespace.eval_markdown else None),
        workspace_root=(Path(namespace.workspace_root) if namespace.workspace_root else None),
        keep_workspaces=bool(namespace.keep_workspaces),
        record=bool(namespace.eval_record),
        allow_network=bool(namespace.allow_network),
        dry_run=dry_run,
    )
    return Options(
        command=command,
        cwd=Path(namespace.cwd),
        bench=bench,
        suite_given=namespace.suite is not None,
    )


def _telemetry_options(namespace: argparse.Namespace, words: str) -> Options | Usage:
    """``telemetry [status|on|off|show]``. Bare ``telemetry`` means ``status``.

    Defaulting to ``status`` rather than to ``on`` is not a style choice: a verb that
    turns data collection on when the user typed only the noun is a dark pattern.
    """
    verb_word = words or Verb.STATUS.value
    if verb_word not in {member.value for member in Verb}:
        return Usage(
            f"{PROGRAM}: error: unknown telemetry verb {verb_word!r} — expected one of "
            f"{', '.join(member.value for member in Verb)}"
        )
    return Options(
        command=Command.TELEMETRY,
        cwd=Path(namespace.cwd),
        telemetry=TelemetryOptions(
            verb=Verb(verb_word),
            # Replaced in `dispatch` from the discovered Paths, so a test can point the
            # whole thing at a tmp_path without touching a real home directory.
            home=Path(),
            limit=max(0, int(namespace.limit or 0)),
        ),
    )


def _budget(namespace: argparse.Namespace) -> Budget | None:
    """A budget only when a ceiling was actually asked for.

    ``None`` rather than an unbounded ``Budget()`` so "the user set no limit" and
    "the user set a limit that happens to be unbounded" stay distinguishable.
    """
    if all(getattr(namespace, name) is None for name in ("max_tokens", "max_usd", "max_seconds")):
        return None
    return Budget(
        max_tokens=namespace.max_tokens,
        max_usd=namespace.max_usd,
        max_wall_seconds=namespace.max_seconds,
    )


# --------------------------------------------------------------------------- #
# Acting
# --------------------------------------------------------------------------- #


async def dispatch(
    options: Options,
    *,
    streams: Streams,
    environ: Mapping[str, str] | None = None,
    agent: Agent | None = None,
) -> int:
    """Do what the options say. The one place a command line becomes behaviour.

    ``agent`` is injected by ``tests/cli`` and by ``ronin.cli.demo``: a fully
    assembled :class:`~ronin.cli.sdk.Agent` needs a model, and every path below has
    to be reachable without one.
    """
    env = os.environ if environ is None else environ
    if options.command is Command.VERSION:
        streams.out(f"{PROGRAM} {_version()}\n")
        return EXIT_OK
    if options.command is Command.EXPORT:
        return _export(options, streams=streams)
    if options.command is Command.SESSIONS:
        return _sessions(options, streams=streams)

    paths = Paths.discover(options.cwd)
    if options.command in (Command.EVAL, Command.DUEL):
        return await _bench(options, paths, env, streams)
    if options.command is Command.TELEMETRY:
        return _telemetry(options, paths, streams)

    if options.command is Command.DOCTOR:
        report = await run_doctor(
            load_workspace(paths, flags=options.flags, environ=env),
            router=_router_or_none(paths, env),
            environ=env,
        )
        streams.out(report.render())
        return report.exit_code()

    # Before the agent, and whether or not one was injected: the wizard is about the
    # workspace, not about who supplied the session.
    if options.wizard and not paths.ronin_dir.exists():
        _first_run(paths, streams)

    if agent is None:
        built = await _open_agent(options, paths, env, streams)
        if built is None:
            return EXIT_ERROR
        agent = built
        owns = True
    else:
        owns = False

    try:
        resumed = _resume(options, paths, agent, streams)
        if isinstance(resumed, Usage):
            streams.err(resumed.message + "\n")
            return resumed.exit_code
        for note in agent.loaded.notes:
            streams.err(note.line() + "\n")
        if options.headless:
            return await _headless(options, agent, streams)
        return await _interactive(options, agent, streams)
    finally:
        if owns:
            await agent.aclose()


async def _bench(
    options: Options,
    paths: Paths,
    env: Mapping[str, str],
    streams: Streams,
) -> int:
    """``eval``/``duel``: resolve the suite path, run, print.

    Deliberately *before* the first-run wizard in :func:`dispatch`. An eval run does
    not need ``.ronin/`` and must not create it: the workspaces it measures in are
    throwaway copies, and writing config into someone's repository as a side effect of
    asking for a benchmark is a surprise.
    """
    bench = options.bench
    if bench is None:  # pragma: no cover - parse always supplies one for these
        streams.err(f"{PROGRAM}: internal error: {options.command.value} without options\n")
        return EXIT_ERROR
    if not options.suite_given:
        bench = replace(bench, suite=default_suite(paths))

    run = run_eval if options.command is Command.EVAL else run_duel_command
    code, out, err = await run(bench, paths, env)
    if err:
        streams.err(err)
    if out:
        streams.out(out)
    return code


def _telemetry(options: Options, paths: Paths, streams: Streams) -> int:
    """``telemetry``: resolve ``home`` from the discovered paths, then run the verb.

    Before the wizard, like ``eval``: someone checking what a tool collects about them
    should not have their repository written to as a side effect of asking.
    """
    settings = options.telemetry
    if settings is None:  # pragma: no cover - parse always supplies one here
        streams.err(f"{PROGRAM}: internal error: telemetry without options\n")
        return EXIT_ERROR
    code, out, err = run_telemetry(replace(settings, home=paths.home))
    if err:
        streams.err(err)
    if out:
        streams.out(out)
    return code


def _disclose_telemetry_once(paths: Paths, streams: Streams) -> None:
    """Print the one-line disclosure on first run, to **stderr**, then never again.

    stderr rather than stdout because stdout is the agent's answer and may be piped
    into something that parses it; a privacy notice appearing inside a JSON payload
    would be both useless and a bug.

    Only when consent is ``UNASKED``. It is not a prompt — nothing blocks and nothing
    is enabled — because a first run that interrogates you before doing the thing you
    asked for is worse than one line you can read afterwards. Telemetry stays off
    until somebody types ``ronin telemetry on``.
    """
    from ..telemetry import Consent, ConsentStore, default_consent_path

    store = ConsentStore(default_consent_path(paths.home))
    record = store.read()
    if record.state is not Consent.UNASKED or record.disclosed:
        return
    streams.err(DISCLOSURE + "\n")
    # Marked after printing: if the write fails the user sees the line again next
    # time, which is the harmless direction to fail in.
    store.mark_disclosed()


def _version() -> str:
    """The installed version, or a named unknown. Never a made-up number."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ronin")
    except PackageNotFoundError:
        return "(not installed; running from a source tree)"


def _router_or_none(paths: Paths, env: Mapping[str, str]) -> Router | None:
    """A router when one is configured. ``None`` is a legitimate answer for ``doctor``.

    ``doctor``'s model check reports "no router configured" itself, and it is exactly
    the diagnostic a first-run user needs — so refusing to run ``doctor`` because
    there is no config would withhold the answer to the question they are asking.
    """
    try:
        return load_router(paths, environ=env)
    except (FileNotFoundError, ProviderError):
        return None


def _first_run(paths: Paths, streams: Streams) -> None:
    """Show the plan, ask, then apply it. The asking lives here on purpose.

    ``wizard`` owns the plan/apply split (``plan_first_run`` touches nothing,
    ``apply_plan`` writes); this owns the question, because a module that prompts is a
    module that cannot be tested without a terminal. A ``no`` is respected and the
    session continues on defaults — the wizard is a convenience, not a gate.
    """
    plan = plan_first_run(paths)
    if plan.empty:
        return
    streams.out("first run in this workspace.\n")
    streams.out(plan.render())
    answer = streams.ask(WIZARD_QUESTION).strip().lower()
    if answer not in ("", "y", "yes"):
        streams.out("nothing written. `ronin doctor` shows the defaults in use.\n")
        return
    written = apply_plan(plan)
    if not written:
        streams.out("nothing needed writing.\n")
        return
    for path in written:
        streams.out(f"wrote {path}\n")


async def _open_agent(
    options: Options, paths: Paths, env: Mapping[str, str], streams: Streams
) -> Agent | None:
    """Assemble the agent, turning every startup failure into a message and ``None``."""
    try:
        router = load_router(paths, environ=env)
    except FileNotFoundError as exc:
        streams.err(f"{exc}\n")
        return None
    try:
        agent = await Agent.open(
            options.cwd,
            router=router,
            mode=options.mode,
            environ=env,
            record=options.record,
            connect_mcp=options.connect_mcp,
        )
    except (OSError, ValueError) as exc:
        streams.err(f"{PROGRAM}: could not start a session: {exc}\n")
        return None
    return agent


def _resume(
    options: Options, paths: Paths, agent: Agent, streams: Streams
) -> ResumedSession | Usage | None:
    """Seed the conversation from a recorded session, if one was asked for.

    Restores the *messages and the spend*, and reports every caveat the replay
    carried — a resumed session with three synthesized tool results is not the same
    thing as a clean one, and a user who is not told will read the difference as the
    model forgetting.
    """
    if options.resume is None:
        return None
    directory = paths.sessions_dir
    try:
        found = (
            resume_session(directory, options.resume)
            if options.resume
            else resume_latest(directory, str(paths.cwd))
        )
    except (OSError, RuntimeError) as exc:
        return Usage(f"{PROGRAM}: cannot resume: {exc}")
    if found is None:
        return Usage(
            f"{PROGRAM}: no recorded session in {paths.cwd} to continue. "
            f"`{PROGRAM} sessions` lists what is there."
        )
    agent.conversation.resume_from(found.state)
    streams.err(
        f"resumed {found.meta.session_id}: {len(found.replay.turns)} turn(s), "
        f"{len(found.state.messages)} message(s)\n"
    )
    for caveat in (*found.skipped, *found.replay.repairs, *found.replay.notes):
        streams.err(f"note: {caveat}\n")
    return found


async def _headless(options: Options, agent: Agent, streams: Streams) -> int:
    """``ronin -p``. The exit code is ``run_headless``'s, unchanged."""
    result = await run_headless(
        agent.stream(
            options.prompt,
            budget=options.budget,
            max_iterations=options.max_iterations,
            verify=options.verify,
        ),
        output_format=options.output_format,
        write=streams.out,
        write_error=streams.err,
        flush=streams.flush,
    )
    for note in agent.conversation.notes:
        streams.err(f"note: {note}\n")
    return result.exit_code


async def _interactive(options: Options, agent: Agent, streams: Streams) -> int:
    """The default path: the Textual view for a given prompt, else a line session.

    ``ui.app.Session`` consumes one ``AsyncIterator[Event]`` and exposes no input
    widget, so a *multi-turn* Textual conversation is not expressible from here
    without changing ``ronin.ui.app``. Rather than pretend, the rule is explicit: a
    prompt on argv gets the Textual view for that turn, and a session with no prompt
    gets the line REPL, which can actually read the next request.
    """
    if options.tui and options.prompt and streams.isatty:
        if not textual_available():
            streams.err(TEXTUAL_MISSING + "\n")
        else:
            return await run_app(
                AppSession(
                    events=agent.stream(
                        options.prompt,
                        budget=options.budget,
                        max_iterations=options.max_iterations,
                        verify=options.verify,
                    ),
                    cwd=str(agent.loaded.paths.cwd),
                    mode=agent.loaded.mode,
                )
            )
    elif options.tui and streams.isatty and not options.prompt:
        streams.err(NO_TUI + "\n")
    return await _repl(options, agent, streams)


async def _repl(options: Options, agent: Agent, streams: Streams) -> int:
    """A line-oriented session: read a request, stream the turn, repeat.

    Deliberately plain — it exists so a bare install (and every test) has a working
    interactive path, and so the slash commands are reachable without Textual.
    """
    streams.out(REPL_BANNER + "\n")
    exit_code = EXIT_OK
    pending = options.prompt
    while True:
        line = pending or streams.ask("> ")
        pending = ""
        if not line:  # EOF, not an empty line: readline() returns "" only at EOF
            streams.out("\n")
            return exit_code
        request = line.strip()
        if not request:
            continue
        if request in ("exit", "quit"):
            return exit_code
        if is_command(request):
            handled = await _slash(request, agent, streams)
            if handled is None:
                return exit_code
            continue
        exit_code = await _one_turn(options, agent, streams, request)


async def _one_turn(options: Options, agent: Agent, streams: Streams, request: str) -> int:
    """Stream one turn to the terminal, and report what it exited as."""
    state = ViewState()
    # See ApprovalTracker: counting raw requests made every approved gated call exit 2.
    tracker = ApprovalTracker()
    # Only this turn's notes: the list accumulates across the session, and reprinting
    # turn one's degradation after every later turn trains the user to ignore it.
    before = len(agent.conversation.notes)
    async for event in agent.stream(
        request,
        budget=options.budget,
        max_iterations=options.max_iterations,
        verify=options.verify,
    ):
        state = reduce_event(state, event)
        _print_event(event, streams)
        tracker.observe(event)
    streams.out("\n")
    for note in agent.conversation.notes[before:]:
        streams.err(f"note: {note}\n")
    return exit_code_for(state, tracker.resolve())


def _print_event(event: Event, streams: Streams) -> None:
    """One event as terminal output. Prose streams; everything else is one line.

    Every string here is model- or tool-derived, so each goes through
    ``strip_controls`` — this path writes to a terminal without a ``Styles`` map, so it
    does not inherit the stripping that :meth:`ronin.ui.render.Styles.text` gives every
    renderer. The approval line matters most: a user is being asked to read a command
    and say yes, and output able to move the cursor could paint over the thing they are
    agreeing to.
    """
    if isinstance(event, TextDelta):
        if not event.thinking:
            streams.out(strip_controls(event.text))
            streams.flush()
    elif isinstance(event, ToolStart):
        arguments = strip_controls(summarize_arguments(event.arguments))
        streams.out(f"\n  · {event.name}({arguments})\n")
    elif isinstance(event, ToolEnd):
        streams.out(f"  → {strip_controls(summarize_result(event.result))}\n")
    elif isinstance(event, ApprovalRequest):
        streams.out(f"\n  ? approval: {strip_controls(event.rendered)}\n")
    elif isinstance(event, Error):
        streams.err(f"\nerror [{event.kind}]: {strip_controls(event.message)}\n")


async def _slash(line: str, agent: Agent, streams: Streams) -> bool | None:
    """Run one slash command. ``None`` means "leave the session".

    Only the commands this build can actually honour are implemented; the rest say
    so by name. A command that silently does nothing is worse than one that admits
    it is not wired.
    """
    parsed = parse_command(line, registry=BUILTIN_REGISTRY)
    if isinstance(parsed, ParseError):
        streams.err(parsed.display + "\n")
        return True
    name = parsed.name
    if name == "help":
        streams.out(render_help(BUILTIN_REGISTRY) + "\n")
    elif name == "doctor":
        report = await run_doctor(agent.loaded, runtime=agent.runtime)
        streams.out(report.render())
    elif name == "clear":
        agent.reset()
        streams.out("transcript dropped; the workspace and tools are unchanged.\n")
    elif name == "cost":
        budget = agent.conversation.budget
        streams.out(
            f"{budget.spent_tokens:,} tokens, ${budget.spent_usd:.4f}, "
            f"{budget.elapsed_seconds:.0f}s\n"
        )
    elif name == "diff":
        result = await agent.runtime.checkpoints.session_diff()
        streams.out((result.diff if result.ok else result.detail) + "\n")
    elif name == "undo":
        checkpoints = agent.conversation.checkpoints
        if not checkpoints:
            streams.err("no checkpoint was taken this session, so there is nothing to undo.\n")
        else:
            restored = await agent.runtime.checkpoints.restore(checkpoints[-1].id)
            streams.out(
                f"restored to {restored.restored_to[:10]}\n"
                if restored.ok
                else restored.detail + "\n"
            )
    else:
        streams.err(
            f"/{name} is a real command but is not wired into this line session. "
            f"`{PROGRAM} --help` lists what is.\n"
        )
    return True


def _export(options: Options, *, streams: Streams) -> int:
    """``ronin export [ID]`` — markdown or html for a recorded session."""
    paths = Paths.discover(options.cwd)
    directory = paths.sessions_dir
    session_id = options.export_session
    if not session_id:
        rows = list_sessions(directory)
        if not rows:
            streams.err(f"{PROGRAM}: no sessions recorded in {directory}\n")
            return EXIT_ERROR
        session_id = rows[0].session_id
    try:
        found = resume_session(directory, session_id)
    except (OSError, RuntimeError) as exc:
        streams.err(f"{PROGRAM}: cannot export {session_id}: {exc}\n")
        return EXIT_ERROR
    render = (
        export_module.to_html
        if options.export_format is ExportFormat.HTML
        else export_module.to_markdown
    )
    body = render(found.events, found.meta)
    if options.export_out is None:
        streams.out(body if body.endswith("\n") else body + "\n")
        return EXIT_OK
    try:
        options.export_out.write_text(body, encoding="utf-8", newline="\n")
    except OSError as exc:
        streams.err(f"{PROGRAM}: cannot write {options.export_out}: {exc}\n")
        return EXIT_ERROR
    streams.out(f"wrote {options.export_out}\n")
    return EXIT_OK


def _sessions(options: Options, *, streams: Streams) -> int:
    """``ronin sessions`` — the picker's rows, newest first, or a search, or a reindex.

    The plain listing still reads the sidecars through ``list_sessions`` rather than
    the index, and that is the point of the index being a cache: the command a user
    runs most often does not depend on it, so a missing or broken database costs them
    nothing. ``--search`` and ``--by-cost`` are the two questions the filesystem cannot
    answer, and those are the ones that go to sqlite.
    """
    paths = Paths.discover(options.cwd)
    wanted = options.sessions or SessionsOptions()
    if wanted.reindex:
        return _reindex(paths.sessions_dir, streams=streams)
    if wanted.search:
        return _search_sessions(paths.sessions_dir, wanted, streams=streams)
    if wanted.by_cost:
        return _sessions_by_cost(paths.sessions_dir, wanted, streams=streams)

    rows = list_sessions(paths.sessions_dir)
    if not rows:
        streams.out(f"no sessions recorded in {paths.sessions_dir}\n")
        return EXIT_OK
    for meta in rows:
        if not meta.loadable:
            # Listed rather than hidden: the session is on disk and this build cannot
            # read it, and those are different facts. Its counters are unknown, so the
            # row shows the reason where the numbers would go instead of printing
            # zeroes that would read as an empty session.
            streams.out(f"{meta.session_id}  unreadable: {meta.unreadable}\n")
            continue
        mark = " (stale)" if meta.stale else ""
        streams.out(
            f"{meta.session_id}  {meta.turns:>4} turn(s)  ${meta.cost_usd:.4f}  {meta.cwd}{mark}\n"
        )
    return EXIT_OK


def _reindex(directory: Path, *, streams: Streams) -> int:
    """``ronin sessions --reindex`` — rebuild the index from the transcripts.

    The whole recovery procedure for the index, which is why every error message that
    mentions the index mentions this flag. Exits 0 even when sessions were skipped: the
    rebuild did what it could and said what it could not, and a non-zero exit would
    make one unreadable session look like a failed command.
    """
    index = SessionIndex.open(directory)
    report = index.rebuild(directory)
    streams.out(f"{report.summary()} in {index.path}\n")
    for note in report.skipped:
        streams.err(f"  skipped {note}\n")
    for problem in index.problems:
        streams.err(f"  {problem}\n")
    return EXIT_OK


def _search_sessions(directory: Path, wanted: SessionsOptions, *, streams: Streams) -> int:
    """``ronin sessions --search TEXT`` — fts5 over recorded prompts and answers."""
    index = SessionIndex.open(directory)
    try:
        hits = index.search(wanted.search, limit=wanted.limit)
    except SessionIndexError as exc:
        # A read failure is reported, never rendered as "no matches" — see the argument
        # in `persistence.index`. The fallback is named because it always works.
        streams.err(f"{PROGRAM}: {exc}\n")
        streams.err(f"{PROGRAM}: `{PROGRAM} sessions --reindex` rebuilds it.\n")
        return EXIT_ERROR
    if not hits:
        empty = index.count() == 0
        streams.out(
            f"no match for {wanted.search!r}"
            + (
                f" — nothing is indexed yet; `{PROGRAM} sessions --reindex` indexes the "
                f"transcripts already on disk\n"
                if empty
                else "\n"
            )
        )
        return EXIT_OK
    for hit in hits:
        streams.out(f"{hit.session_id}  turn {hit.turn:>3}  {hit.role:<9} {hit.snippet}\n")
    return EXIT_OK


def _sessions_by_cost(directory: Path, wanted: SessionsOptions, *, streams: Streams) -> int:
    """``ronin sessions --by-cost`` — the ``ORDER BY`` the filesystem cannot do."""
    index = SessionIndex.open(directory)
    try:
        rows = index.costliest(limit=wanted.limit)
    except SessionIndexError as exc:
        streams.err(f"{PROGRAM}: {exc}\n")
        return EXIT_ERROR
    if not rows:
        streams.out(
            f"nothing is indexed yet — `{PROGRAM} sessions --reindex` indexes the "
            f"transcripts already on disk\n"
        )
        return EXIT_OK
    for meta in rows:
        streams.out(
            f"{meta.session_id}  ${meta.cost_usd:>9.4f}  {meta.turns:>4} turn(s)  {meta.cwd}\n"
        )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    """``ronin``'s entry point. Returns the exit code; never calls ``sys.exit``."""
    arguments = sys.argv[1:] if argv is None else list(argv)
    parsed = parse(arguments)
    streams = Streams.standard()
    if isinstance(parsed, Usage):
        writer = streams.out if parsed.to_stdout else streams.err
        if parsed.message:
            writer(parsed.message if parsed.message.endswith("\n") else parsed.message + "\n")
        return parsed.exit_code
    # The one-time telemetry notice lives here rather than in `dispatch`, and that is
    # not a style choice. `Paths.discover()` defaults `home` to `Path.home()`, so a
    # disclosure written from `dispatch` writes to the *real* home directory of whoever
    # is running — including a test suite. It did: the first run of the suite created
    # ~/.ronin/telemetry.json on the developer's machine, and then whichever test ran
    # first consumed the once-only notice, making a later assertion order-dependent.
    #
    # `main` is the process entry point and the only place that legitimately resolves a
    # real home, so the notice belongs here. Every test that calls `dispatch` stays
    # hermetic, and `_disclose_telemetry_once` is still unit-tested directly with an
    # injected `Paths`.
    if parsed.command is Command.RUN:
        _disclose_telemetry_once(Paths.discover(parsed.cwd), streams)
    try:
        return asyncio.run(dispatch(parsed, streams=streams))
    except KeyboardInterrupt:
        # A ctrl-c at the prompt is a normal way to leave, not a traceback.
        streams.err("\ninterrupted\n")
        return EXIT_ERROR


__all__ = [
    "EXIT_USAGE",
    "PROGRAM",
    "REPL_BANNER",
    "WIZARD_QUESTION",
    "Command",
    "ExportFormat",
    "Options",
    "Streams",
    "Usage",
    "build_parser",
    "dispatch",
    "main",
    "parse",
]


if __name__ == "__main__":
    raise SystemExit(main())
