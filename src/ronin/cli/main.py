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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import TextIO

from ..agents.hooks import MATCH_ALL
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
from ..mcp.jsonrpc import TransportClosed
from ..mcp.server import serve_stdio
from ..mcp.transport import StreamPair, stdio_streams
from ..persistence import export as export_module
from ..persistence.index import SessionIndex, SessionIndexError
from ..persistence.resume import ResumedSession, resume_latest, resume_session
from ..persistence.transcript import list_sessions
from ..providers.router import Role as ModelRole
from ..providers.router import Router
from ..providers.types import ProviderError
from ..safety.policy import Asker
from ..telemetry import DISCLOSURE
from ..ui.app import TEXTUAL_MISSING, multi_turn_events, run_app, textual_available
from ..ui.app import Session as AppSession
from ..ui.commands import ParseError, is_command, render_help
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
from .approve import Handoff, PromptAsker
from .bench import BenchOptions, default_suite, run_duel_command, run_eval
from .detect import Detection, detect, real_probe
from .doctor import run_doctor
from .harvest import HarvestOptions, run_harvest
from .mcp_auth import SUBCOMMANDS as MCP_SUBCOMMANDS
from .mcp_auth import McpLoginOptions, run_mcp_login
from .repo import SUBCOMMANDS as REPO_SUBCOMMANDS
from .repo import RepoOptions, run_repo
from .sdk import Agent, load_router
from .serve import build_server
from .spine import Paths
from .status import context_share, current_branch
from .stream import DEFAULT_MAX_ITERATIONS, CompactionPairingError
from .telemetry_cmd import TelemetryOptions, Verb
from .telemetry_cmd import run as run_telemetry
from .wire import load_workspace
from .wizard import apply_plan, plan_config, plan_first_run, run_smoke, write_config

PROGRAM = "ronin"

#: Exit code for a malformed command line. Deliberately not argparse's 2 — see the
#: module docstring.
EXIT_USAGE = EXIT_ERROR

#: What the wizard is asked before it writes anything. A first run that creates
#: files in someone's repository without asking is a first run they do not trust.
WIZARD_QUESTION = "set up .ronin/ for this workspace? [Y/n] "

TUI_QUIT_NOTE = "press ctrl+c to leave."

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
    HARVEST = "harvest"
    TELEMETRY = "telemetry"
    MCP_SERVE = "mcp-serve"
    MCP = "mcp"
    ACP = "acp"
    API = "api"
    PLUGIN = "plugin"
    REPO = "repo"


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
class Slash:
    """What a slash command asks the session to do next.

    Three outcomes rather than a boolean, because a user-defined command is a *prompt*:
    ``.ronin/commands/review.md`` expands to text the model should act on, and the
    dispatcher cannot run a turn itself — it has no budget, no iteration cap and no verify
    flag. So it returns the request and the session runs it.
    """

    #: Non-empty: run this as a turn, exactly as if it had been typed.
    prompt: str = ""
    #: End the session. Distinct from "nothing to do" so an exit cannot be a fallthrough.
    leave: bool = False


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
    #: Present for ``harvest`` only, same rule as ``bench``: ``None`` everywhere else so
    #: a path that reads it without checking the command fails loudly.
    harvest: HarvestOptions | None = None
    #: Whether ``--suite`` was passed. Distinguishes "the user chose this path" from
    #: "nobody said, so use the workspace default", which a bare ``Path()`` cannot.
    suite_given: bool = False
    #: Present for ``telemetry`` only, same rule as ``bench``.
    telemetry: TelemetryOptions | None = None
    #: Present for ``sessions`` only, same rule again.
    sessions: SessionsOptions | None = None
    #: ``api`` bind address. Loopback by default: an agentic endpoint that runs the
    #: whole loop must not be reachable off the machine unless the operator says so.
    host: str = "127.0.0.1"
    port: int = 8080
    #: ``plugin`` verb and its argument (``add <source>`` / ``list``).
    plugin_verb: str = ""
    plugin_source: str = ""
    #: Present for ``repo`` only, same rule as ``bench``: ``None`` everywhere else so a
    #: path that reads it without checking the command fails loudly.
    repo: RepoOptions | None = None
    #: Present for ``mcp login`` only; ``None`` everywhere else, same fail-loud rule.
    mcp_login: McpLoginOptions | None = None

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
            "  harvest --tasks POOL --out DIR\n"
            "                             run a task pool, record trajectories, and "
            "write\n"
            "                             SFT/DPO/RLVR training datasets\n"
            "  telemetry [status|on|off|show]\n"
            "                             opt-in aggregate task outcomes; off by "
            "default\n"
            "  mcp-serve                  serve this workspace's tools over MCP on "
            "stdio, so\n"
            "                             claude desktop / cursor / another ronin can "
            "drive it.\n"
            "                             --mode decides what is exposed: ask gives "
            "the read-only\n"
            "                             tools, auto_edit adds edit, full adds bash "
            "and ronin_task\n"
            "  acp                        serve the Agent Client Protocol over stdio, "
            "so zed /\n"
            "                             jetbrains / any ACP editor drives a ronin "
            "session\n"
            "  api [--host H --port N]    OpenAI- and Anthropic-compatible /v1 "
            "endpoints that\n"
            "                             run the agentic loop (loopback by default)\n"
            "  plugin add PATH | list     install a local plugin bundle (skills, mcp, "
            "agents,\n"
            "                             commands, hooks) or list installed ones\n"
            "\n"
            "eval/duel flags: --suite PATH, --model NAME (repeat for duel), "
            "--parallel N,\n"
            "  --category C, --tag T, --task ID, --regression-gate, --limit N, "
            "--seed N,\n"
            "  --json FILE, --markdown FILE, --keep-workspaces, --record, "
            "--allow-network.\n"
            "  --dry-run lists what would run and opens no model, so it needs no key.\n"
            "\n"
            "harvest flags: --tasks POOL, --out DIR, --model NAME, --parallel N, "
            "--container,\n"
            "  the eval selection flags (--category/--tag/--task/--limit), and "
            "--dry-run.\n"
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
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        metavar="HOST",
        help="bind address for `api` (default: 127.0.0.1, loopback only)",
    )
    parser.add_argument(
        "--port", type=int, default=8080, metavar="N", help="bind port for `api` (default: 8080)"
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

    # harvest. Its selection flags (--category/--tag/--task/--limit/--model/--parallel/
    # --workspaces/--keep-workspaces/--allow-network) are the eval ones above; these
    # three are harvest's own.
    parser.add_argument(
        "--tasks",
        dest="harvest_tasks",
        default=None,
        metavar="POOL",
        help="task pool root to harvest (never the eval suite)",
    )
    # harvest's dataset output dir reuses export's --out/-o rather than defining a
    # second spelling for "where the output goes".
    parser.add_argument(
        "--container",
        action="store_true",
        help="run each task's verify.sh in docker (harvest); fails closed with no daemon",
    )
    parser.add_argument(
        "--root",
        dest="repo_root",
        default=None,
        metavar="DIR",
        help="repo root to analyse (repo); defaults to the working directory",
    )
    parser.add_argument(
        "--top",
        dest="repo_top",
        type=int,
        default=None,
        metavar="N",
        help="how many top-ranked modules `repo map` lists",
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
        Command.HARVEST.value,
        Command.TELEMETRY.value,
        Command.MCP_SERVE.value,
        Command.MCP.value,
        Command.ACP.value,
        Command.API.value,
        Command.PLUGIN.value,
        Command.REPO.value,
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
    if command is Command.HARVEST:
        return _harvest_options(namespace, words)
    if command is Command.TELEMETRY:
        return _telemetry_options(namespace, words)
    if command is Command.SESSIONS:
        return _sessions_options(namespace, words)
    if command is Command.MCP_SERVE:
        return _mcp_serve_options(namespace, words)
    if command is Command.MCP:
        return _mcp_login_options(namespace, words)
    if command is Command.ACP:
        return _acp_options(namespace, words)
    if command is Command.API:
        return _api_options(namespace, words)
    if command is Command.PLUGIN:
        return _plugin_options(namespace, words)
    if command is Command.REPO:
        return _repo_options(namespace, words)

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


def _mcp_serve_options(namespace: argparse.Namespace, words: str) -> Options | Usage:
    """``mcp-serve`` argv. Takes no prompt, and says so rather than ignoring one.

    A prompt is refused instead of dropped because ``ronin2 mcp-serve "fix the test"``
    reads as though it would run something — and a server that silently discarded the
    request would sit there answering frames while its user waited for an answer.

    ``--print`` and ``--output-format`` are refused for the same reason and one more:
    both write to stdout, and stdout is the JSON-RPC frame stream. A single line of
    prose there corrupts the session for the client at the other end.
    """
    if words:
        return Usage(
            f"{PROGRAM}: error: mcp-serve takes no prompt (got {words!r}); it serves "
            f"this workspace's tools to whatever launched it. Use `{PROGRAM} -p` to run "
            "a prompt yourself."
        )
    if namespace.print_prompt is not None:
        return Usage(f"{PROGRAM}: error: mcp-serve does not take --print")
    if namespace.output_format is not None:
        return Usage(
            f"{PROGRAM}: error: mcp-serve does not take --output-format; its stdout "
            "carries JSON-RPC frames and nothing else"
        )
    return Options(
        command=Command.MCP_SERVE,
        mode=Mode(namespace.mode) if namespace.mode else None,
        yolo=bool(namespace.yolo),
        sandbox=bool(namespace.sandbox),
        cwd=Path(namespace.cwd),
        record=bool(namespace.record),
        connect_mcp=bool(namespace.connect_mcp),
        # The first-run wizard prints a question to stdout and reads the answer from
        # stdin. Both are the protocol here, so it is not merely unhelpful — it would
        # write a prompt into the frame stream and then consume the client's first
        # request as the answer. Off unconditionally, not by flag.
        wizard=False,
    )


def _acp_options(namespace: argparse.Namespace, words: str) -> Options | Usage:
    """``acp`` argv. Like ``mcp-serve``: stdio is the wire, so no prompt and no stdout.

    An editor (Zed, JetBrains) launches ``ronin acp`` and speaks the Agent Client
    Protocol over stdin/stdout. A prompt, ``--print`` or ``--output-format`` would all
    put prose where the JSON-RPC frames go, so each is refused rather than dropped.
    """
    if words:
        return Usage(
            f"{PROGRAM}: error: acp takes no prompt (got {words!r}); an ACP client drives "
            "the session over stdio"
        )
    if namespace.print_prompt is not None:
        return Usage(f"{PROGRAM}: error: acp does not take --print")
    if namespace.output_format is not None:
        return Usage(
            f"{PROGRAM}: error: acp does not take --output-format; its stdout carries "
            "JSON-RPC frames and nothing else"
        )
    return Options(
        command=Command.ACP,
        mode=Mode(namespace.mode) if namespace.mode else None,
        cwd=Path(namespace.cwd),
        record=bool(namespace.record),
        connect_mcp=bool(namespace.connect_mcp),
        wizard=False,  # prompts on stdout / reads stdin — both are the protocol
    )


def _api_options(namespace: argparse.Namespace, words: str) -> Options | Usage:
    """``api`` argv: bind an OpenAI/Anthropic-compatible HTTP endpoint.

    Takes ``--host``/``--port``, not a prompt. The endpoints run the whole agentic loop
    per request; there is no single prompt to give here.
    """
    if words:
        return Usage(
            f"{PROGRAM}: error: api takes no prompt (got {words!r}); it serves the "
            "OpenAI/Anthropic-compatible endpoints on --host/--port"
        )
    if namespace.print_prompt is not None:
        return Usage(f"{PROGRAM}: error: api does not take --print")
    if namespace.port < 1 or namespace.port > 65535:
        return Usage(f"{PROGRAM}: error: --port must be between 1 and 65535")
    return Options(
        command=Command.API,
        mode=Mode(namespace.mode) if namespace.mode else None,
        cwd=Path(namespace.cwd),
        record=bool(namespace.record),
        connect_mcp=bool(namespace.connect_mcp),
        host=str(namespace.host),
        port=int(namespace.port),
        wizard=False,
    )


def _plugin_options(namespace: argparse.Namespace, words: str) -> Options | Usage:
    """``plugin add <source>`` / ``plugin list``. A verb plus, for ``add``, a path.

    ``add`` takes a **local** path — a directory holding a plugin. Cloning a git URL is
    out of scope here (offline), so a URL is refused with the reason rather than a
    confusing failure deep in a copy.
    """
    parts = words.split(maxsplit=1)
    verb = parts[0] if parts else ""
    argument = parts[1].strip() if len(parts) > 1 else ""
    if verb not in {"add", "list"}:
        return Usage(
            f"{PROGRAM}: error: plugin takes a verb — `plugin add <path>` installs a "
            "local plugin directory, `plugin list` shows what is installed"
        )
    if verb == "add" and not argument:
        return Usage(f"{PROGRAM}: error: plugin add needs a path to a plugin directory")
    if verb == "add" and "://" in argument:
        return Usage(
            f"{PROGRAM}: error: plugin add takes a local path, not a URL. Clone the repo "
            "first, then `plugin add <the cloned directory>`."
        )
    return Options(
        command=Command.PLUGIN,
        cwd=Path(namespace.cwd),
        plugin_verb=verb,
        plugin_source=argument,
        wizard=False,
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


def _harvest_options(namespace: argparse.Namespace, words: str) -> Options | Usage:
    """``harvest`` argv into :class:`HarvestOptions`, carried on :class:`Options`.

    ``--tasks`` is required (there is no default pool — the eval suite is refused as
    input), and ``--out`` is required for a real run but not for ``--dry-run``, which
    writes nothing. The selection flags are shared with ``eval``; harvest reuses their
    parsed values rather than defining a second spelling.
    """
    if words:
        return Usage(
            f"{PROGRAM}: error: harvest takes flags, not a prompt (got {words!r}). "
            "Select tasks with --task/--category/--tag."
        )
    if namespace.print_prompt:
        return Usage(f"{PROGRAM}: error: harvest does not take --print")
    if namespace.parallel < 1:
        return Usage(f"{PROGRAM}: error: --parallel must be at least 1")
    if namespace.limit is not None and namespace.limit < 1:
        return Usage(f"{PROGRAM}: error: --limit must be at least 1")
    if not namespace.harvest_tasks:
        return Usage(f"{PROGRAM}: error: harvest needs --tasks POOL (the pool to run)")

    dry_run = bool(namespace.dry_run)
    models = tuple(namespace.models or ())
    if len(models) > 1:
        return Usage(f"{PROGRAM}: error: harvest runs one model (got {len(models)})")
    if not dry_run and not namespace.export_out:
        return Usage(
            f"{PROGRAM}: error: harvest needs --out DIR for the datasets, or --dry-run "
            "to see what would run without writing anything"
        )

    harvest = HarvestOptions(
        tasks=Path(namespace.harvest_tasks),
        # A placeholder under --dry-run, which never writes; replaced by the real path
        # for a run, where it is required above. Reuses export's --out/-o.
        out=Path(namespace.export_out) if namespace.export_out else Path(),
        models=models,
        parallel=int(namespace.parallel),
        ids=frozenset(namespace.task_ids or ()),
        categories=frozenset(namespace.categories or ()),
        tags=frozenset(namespace.eval_tags or ()),
        regression_gate=bool(namespace.regression_gate),
        limit=namespace.limit,
        workspace_root=(Path(namespace.workspace_root) if namespace.workspace_root else None),
        keep_workspaces=bool(namespace.keep_workspaces),
        container=bool(namespace.container),
        allow_network=bool(namespace.allow_network),
        dry_run=dry_run,
    )
    return Options(command=Command.HARVEST, cwd=Path(namespace.cwd), harvest=harvest)


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


def _repo_options(namespace: argparse.Namespace, words: str) -> Options | Usage:
    """``repo <subcommand> [path]`` — a read-only analysis verb.

    The subcommand and (for ``explain``) the target file come through the shared
    ``words`` positional; ``--root`` overrides where to scan, ``--top`` sizes the map
    listing, and ``--output-format json`` switches the render — no new format flag.
    """
    parts = words.split()
    if not parts:
        return Usage(f"{PROGRAM} repo: needs a subcommand ({', '.join(REPO_SUBCOMMANDS)})\n")
    subcommand, *rest = parts
    if subcommand not in REPO_SUBCOMMANDS:
        return Usage(
            f"{PROGRAM} repo: unknown subcommand {subcommand!r}; "
            f"expected one of {', '.join(REPO_SUBCOMMANDS)}\n"
        )
    target = rest[0] if rest else ""
    if subcommand == "explain" and not target:
        return Usage(f"{PROGRAM} repo explain: needs a file path\n")
    root = Path(namespace.repo_root) if namespace.repo_root else Path(namespace.cwd)
    as_json = namespace.output_format == OutputFormat.JSON.value
    if namespace.repo_top is None:
        repo = RepoOptions(subcommand=subcommand, root=root, target=target, as_json=as_json)
    else:
        if namespace.repo_top <= 0:
            return Usage(f"{PROGRAM} repo: --top must be a positive integer\n")
        repo = RepoOptions(
            subcommand=subcommand,
            root=root,
            target=target,
            top=namespace.repo_top,
            as_json=as_json,
        )
    return Options(command=Command.REPO, cwd=Path(namespace.cwd), repo=repo)


def _mcp_login_options(namespace: argparse.Namespace, words: str) -> Options | Usage:
    """``mcp login <server>`` — run the attended OAuth flow for one configured server.

    A subcommand group (only ``login`` for now) rather than a top-level verb, so it reads
    ``ronin mcp login docs`` and leaves room for ``logout``/``status`` later without minting
    a new verb each time.
    """
    parts = words.split()
    if not parts:
        return Usage(f"{PROGRAM} mcp: needs a subcommand ({', '.join(MCP_SUBCOMMANDS)})\n")
    subcommand, *rest = parts
    if subcommand not in MCP_SUBCOMMANDS:
        return Usage(
            f"{PROGRAM} mcp: unknown subcommand {subcommand!r}; "
            f"expected one of {', '.join(MCP_SUBCOMMANDS)}\n"
        )
    if not rest:
        return Usage(f"{PROGRAM} mcp login: needs a server name from .ronin/mcp.json\n")
    login = McpLoginOptions(server=rest[0], root=Path(namespace.cwd))
    return Options(command=Command.MCP, cwd=Path(namespace.cwd), mcp_login=login)


async def dispatch(
    options: Options,
    *,
    streams: Streams,
    environ: Mapping[str, str] | None = None,
    agent: Agent | None = None,
    mcp_streams: StreamPair | None = None,
    acp_streams: StreamPair | None = None,
) -> int:
    """Do what the options say. The one place a command line becomes behaviour.

    ``agent`` is injected by ``tests/cli`` and by ``ronin.cli.demo``: a fully
    assembled :class:`~ronin.cli.sdk.Agent` needs a model, and every path below has
    to be reachable without one.

    ``mcp_streams``/``acp_streams`` are the same idea for ``mcp-serve``/``acp``: the
    pipes each serves on. Injected, each is one end of a
    :func:`~ronin.mcp.transport.memory_duplex` so a real client can drive the whole
    path in a test; omitted, the server wraps this process's own stdin and stdout.
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
    if options.command is Command.HARVEST:
        return await _harvest(options, paths, env, streams)
    if options.command is Command.TELEMETRY:
        return _telemetry(options, paths, streams)
    if options.command is Command.MCP_SERVE:
        return await _mcp_serve(options, paths, env, streams, agent, mcp_streams)
    if options.command is Command.ACP:
        return await _acp(options, paths, env, streams, acp_streams)
    if options.command is Command.API:
        return await _api(options, paths, env, streams)
    if options.command is Command.PLUGIN:
        return _plugin(options, paths, streams)
    if options.command is Command.REPO:
        return _repo(options, streams)
    if options.command is Command.MCP:
        return await _mcp_login(options, env, streams)

    if options.command is Command.DOCTOR:
        report = await run_doctor(
            load_workspace(paths, flags=options.flags, environ=env),
            router=_router_or_none(paths, env),
            environ=env,
            detection=detect(env=env, probe=real_probe),
        )
        streams.out(report.render())
        return report.exit_code()

    # Before the agent, and whether or not one was injected: the wizard is about the
    # workspace, not about who supplied the session. Model detection (which probes
    # loopback ports) and the smoke run only at an interactive terminal — a piped or
    # test run skips them, so nothing here opens a socket off the onboarding path.
    if options.wizard and not paths.ronin_dir.exists():
        detection = detect(env=env, probe=real_probe) if streams.isatty else None
        smoke = _smoke_task(options, paths, env) if streams.isatty else None
        await _first_run(paths, streams, detection=detection, smoke=smoke)

    # Who answers an approval is decided here, before the agent exists, because the
    # policy engine is built with its asker and cannot be handed a different one later.
    # An injected agent keeps whatever it was built with — a caller who assembled the
    # session also chose its consent path, and overriding that from here would be this
    # function deciding who may approve an edit in somebody else's runtime.
    handoff = Handoff() if _wants_modal(options, streams) else None
    if agent is None:
        built = await _open_agent(options, paths, env, streams, asker=_asker(handoff, streams))
        if built is None:
            return EXIT_ERROR
        agent = built
        owns = True
    else:
        handoff = None
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
        return await _interactive(options, agent, streams, handoff)
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


def _repo(options: Options, streams: Streams) -> int:
    """``repo``: scan the tree and render an analysis. Read-only, offline, no wizard.

    Before the first-run wizard in :func:`dispatch`, like ``eval`` and ``harvest``: a
    read-only analysis of some other directory must never write ``.ronin/`` into the
    repo it was pointed at as a side effect of answering a question about it.
    """
    repo = options.repo
    if repo is None:  # pragma: no cover - parse always supplies one for this command
        streams.err(f"{PROGRAM}: internal error: repo without options\n")
        return EXIT_ERROR
    code, out, err = run_repo(repo)
    if err:
        streams.err(err)
    if out:
        streams.out(out)
    return code


async def _mcp_login(options: Options, env: Mapping[str, str], streams: Streams) -> int:
    """``mcp login``: run the attended OAuth flow for one server. Interactive, offline-safe.

    Before the first-run wizard in :func:`dispatch`, like ``repo``: authorizing a server is a
    self-contained action against ``.ronin/mcp.json`` and must not trigger a wizard that
    writes into the repo as a side effect. The real driver opens a browser and writes the OS
    keyring; both are inside the injected driver, so this executor stays a thin edge.
    """
    login = options.mcp_login
    if login is None:  # pragma: no cover - parse always supplies one for this command
        streams.err(f"{PROGRAM}: internal error: mcp login without options\n")
        return EXIT_ERROR
    code, out, err = await run_mcp_login(login, environ=env)
    if err:
        streams.err(err)
    if out:
        streams.out(out)
    return code


async def _harvest(
    options: Options,
    paths: Paths,
    env: Mapping[str, str],
    streams: Streams,
) -> int:
    """``harvest``: run a pool, record trajectories, write datasets.

    Before the first-run wizard for the same reason as :func:`_bench`: a harvest runs
    in throwaway workspaces and must not write ``.ronin/`` into the repository it was
    invoked from as a side effect.
    """
    harvest = options.harvest
    if harvest is None:  # pragma: no cover - parse always supplies one for this command
        streams.err(f"{PROGRAM}: internal error: harvest without options\n")
        return EXIT_ERROR
    code, out, err = await run_harvest(harvest, paths, env)
    if err:
        streams.err(err)
    if out:
        streams.out(out)
    return code


async def _mcp_serve(
    options: Options,
    paths: Paths,
    env: Mapping[str, str],
    streams: Streams,
    agent: Agent | None,
    pipes: StreamPair | None,
) -> int:
    """``mcp-serve``: assemble a session, publish its tools, answer frames until EOF.

    Before the first-run wizard in :func:`dispatch`, like ``eval`` and ``telemetry``,
    and for a stronger reason than either: the wizard prompts on stdout and reads
    stdin, which here are the protocol. :func:`_mcp_serve_options` sets
    ``wizard=False`` as well, so this is true whichever way the options were built.

    Every line this function emits goes to **stderr**. A client reading stdout is
    parsing JSON-RPC frames, and :class:`~ronin.mcp.transport.StdioTransport` — Ronin's
    own client — treats one unexpected line there as fatal, correctly. Returning ``0``
    at EOF is the normal end: the peer closed the pipe because it was finished.
    """
    owns = agent is None
    if agent is None:
        # No asker: there is no human on the other end of stdin to ask. Anything the
        # mode does not already allow is refused as a value, and `serve.build_server`
        # will not have published it in the first place.
        built = await _open_agent(options, paths, env, streams, asker=None)
        if built is None:
            return EXIT_ERROR
        agent = built
    try:
        served = build_server(agent, version=_version())
        for note in agent.loaded.notes:
            streams.err(note.line() + "\n")
        streams.err(served.banner())
        streams.flush()
        if pipes is None:
            try:
                pipes = await stdio_streams()
            except TransportClosed as exc:
                streams.err(f"{PROGRAM}: cannot serve MCP on stdio: {exc}\n")
                return EXIT_ERROR
        await serve_stdio(served.server, pipes)
    finally:
        if owns:
            await agent.aclose()
    return EXIT_OK


async def _acp(
    options: Options,
    paths: Paths,
    env: Mapping[str, str],
    streams: Streams,
    pipes: StreamPair | None,
) -> int:
    """``acp``: serve the Agent Client Protocol over stdio to an editor.

    Unlike ``mcp-serve`` there is no single pre-built agent: ACP opens one Agent per
    ``session/new`` cwd, so this passes a factory rather than an agent. No asker, for
    the same reason as ``mcp-serve`` — stdin is the wire, there is no human on it — so a
    tool the mode does not already allow is refused as a value. Every line here is
    stderr; stdout carries frames.
    """
    from .acp import AcpServer, serve_acp

    async def open_agent(cwd: str) -> Agent:
        discovered = Paths.discover(Path(cwd))
        return await Agent.open(
            cwd,
            router=load_router(discovered, environ=env),
            mode=options.mode,
            environ=env,
            record=options.record,
            connect_mcp=options.connect_mcp,
            asker=None,
        )

    server = AcpServer(open_agent=open_agent, version=_version())
    streams.err("ronin acp: Agent Client Protocol over stdio\n")
    streams.flush()
    if pipes is None:
        try:
            pipes = await stdio_streams()
        except TransportClosed as exc:
            streams.err(f"{PROGRAM}: cannot serve ACP on stdio: {exc}\n")
            return EXIT_ERROR
    await serve_acp(server, pipes)
    return EXIT_OK


async def _api(options: Options, paths: Paths, env: Mapping[str, str], streams: Streams) -> int:
    """``api``: bind the OpenAI/Anthropic-compatible HTTP endpoints.

    Each request opens its own Agent and runs the full loop — the endpoints are
    stateless, so a fresh session per request is the honest model. Loopback by default;
    binding off-box is the operator's explicit ``--host`` choice. Blocks in
    ``serve_forever`` until interrupted.
    """
    from .http_api import serve_http

    try:
        router = load_router(paths, environ=env)
    except FileNotFoundError as exc:
        streams.err(f"{exc}\n")
        return EXIT_ERROR

    async def open_agent() -> Agent:
        return await Agent.open(
            options.cwd,
            router=router,
            mode=options.mode,
            environ=env,
            record=options.record,
            connect_mcp=options.connect_mcp,
            asker=None,
        )

    streams.err(
        f"ronin api: OpenAI/Anthropic-compatible endpoints on "
        f"http://{options.host}:{options.port} (loop runs per request)\n"
    )
    streams.flush()
    try:
        await asyncio.to_thread(
            serve_http, options.host, options.port, open_agent=open_agent, err=streams.err
        )
    except OSError as exc:
        streams.err(f"{PROGRAM}: cannot bind {options.host}:{options.port}: {exc}\n")
        return EXIT_ERROR
    return EXIT_OK


def _plugin(options: Options, paths: Paths, streams: Streams) -> int:
    """``plugin add <path>`` / ``plugin list`` — install and inspect installed plugins.

    ``add`` shows a community bundle's consent summary — the shell hooks and tools it
    wants — and installs only if the human agrees, because a plugin's hooks run
    ``/bin/sh``. ``official``/``trusted`` bundles skip the prompt.
    """
    from ..ext.plugins import PluginConsent, PluginError, install, load_installed

    if options.plugin_verb == "list":
        plugins, notes = load_installed(paths.home)
        if not plugins:
            streams.out("no plugins installed. `ronin plugin add <path>` installs one.\n")
        for plugin in plugins:
            streams.out(
                f"{plugin.name}  [{plugin.trust}]  {plugin.description or plugin.directory}\n"
            )
        for note in notes:
            streams.err(f"note: {note}\n")
        return EXIT_OK

    def approve(consent: PluginConsent) -> bool:
        streams.out(consent.render() + "\n")
        answer = streams.ask(f"enable plugin {consent.name!r}? [y/N] ").strip().lower()
        return answer in ("y", "yes")

    try:
        plugin = install(Path(options.plugin_source), paths.home, approve=approve)
    except (PluginError, OSError) as exc:
        streams.err(f"{PROGRAM}: cannot add plugin: {exc}\n")
        return EXIT_ERROR
    streams.out(
        f"installed {plugin.name} [{plugin.trust}] to {paths.home / '.ronin' / 'plugins'}\n"
    )
    return EXIT_OK


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


async def _first_run(
    paths: Paths,
    streams: Streams,
    *,
    detection: Detection | None = None,
    smoke: Callable[[], Awaitable[bool]] | None = None,
) -> None:
    """Show the plan, ask, apply it, then get the user to a working model.

    ``wizard`` owns the plan/apply split (``plan_first_run`` touches nothing,
    ``apply_plan`` writes); this owns the question, because a module that prompts is a
    module that cannot be tested without a terminal. A ``no`` is respected and the
    session continues on defaults — the wizard is a convenience, not a gate.

    When a ``detection`` is supplied and it found a usable model but no config exists, a
    working ``models.toml`` is written from it — pointing at a detected local server
    (ollama/lmstudio/vllm) or a provider whose key is set — so the common
    "``pipx install`` then nothing works because there is no config" first run ends with
    one that does. The optional ``smoke`` is the 10-second "does a task actually
    complete" check; it is injected so a test never needs a model, and any failure is
    reported as a line, never raised.
    """
    plan = plan_first_run(paths)
    if not plan.empty:
        streams.out("first run in this workspace.\n")
        streams.out(plan.render())
        answer = streams.ask(WIZARD_QUESTION).strip().lower()
        if answer not in ("", "y", "yes"):
            streams.out("nothing written. `ronin doctor` shows the defaults in use.\n")
            return
        written = apply_plan(plan)
        for path in written:
            streams.out(f"wrote {path}\n")
        if not written:
            streams.out("nothing needed writing.\n")

    # Model config: only when detection says a model is actually reachable and the user
    # has none yet. Never overwrites — `write_config` returns None if a config exists.
    if detection is not None and detection.has_any_model():
        body = plan_config(detection)
        if body is not None:
            config_path = write_config(paths, body)
            if config_path is not None:
                streams.out(f"wrote {config_path} (from a detected model)\n")
                if smoke is not None:
                    result = await run_smoke(smoke)
                    streams.out(result.line() + "\n")
    elif detection is not None:
        streams.out(
            "no API key or local model server detected. Set a provider key (e.g. "
            "ANTHROPIC_API_KEY) or run `ollama serve`, then `ronin doctor`.\n"
        )


def _smoke_task(
    options: Options, paths: Paths, env: Mapping[str, str]
) -> Callable[[], Awaitable[bool]]:
    """The 10-second "does a task actually complete" check, as an awaitable.

    Opens an agent from the config the wizard just wrote and asks it one trivial
    question; ``run_smoke`` wraps this with the deadline and turns any failure into a
    reported line. Kept as a closure so the wizard needs no model knowledge and a test
    can pass its own.
    """

    async def smoke() -> bool:
        agent = await _open_agent(options, paths, env, streams=Streams.standard(), asker=None)
        if agent is None:
            return False
        try:
            result = await agent.run("Reply with exactly the word: ok")
            return result.ok and "ok" in result.text.lower()
        finally:
            await agent.aclose()

    return smoke


def _wants_modal(options: Options, streams: Streams) -> bool:
    """Whether this run will show the Textual approval modal.

    The same condition :func:`_interactive` uses to choose the Textual view, asked
    early because the asker has to be built before the agent is. Duplicating the
    condition would be how the two come apart — a run that shows a modal nobody
    collects an answer from, or an asker waiting for a modal that never appears.
    """
    return bool(options.tui and options.prompt and streams.isatty and textual_available())


def _asker(handoff: Handoff | None, streams: Streams) -> Asker | None:
    """Who answers gated calls: the modal, the line prompt, or nobody.

    ``None`` means the default, :class:`~ronin.safety.policy.UnattendedAsker` — every
    gated call refused. That is the right answer for a pipe: an approval prompt written
    to a stream nobody is reading is an approval nobody gave.
    """
    if handoff is not None:
        return handoff
    if streams.isatty:
        return PromptAsker(prompt=streams.ask, write=streams.out)
    return None


async def _open_agent(
    options: Options,
    paths: Paths,
    env: Mapping[str, str],
    streams: Streams,
    *,
    asker: Asker | None = None,
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
            asker=asker,
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


async def _interactive(
    options: Options, agent: Agent, streams: Streams, handoff: Handoff | None = None
) -> int:
    """The default path: the Textual view for a given prompt, else a line session.

    A bare ``ronin`` now opens the Textual view too, waiting for the first message
    rather than running a turn with nothing in it. That used to be impossible and the
    old rule ("no prompt gets the line REPL") was written around the limitation:
    ``Session`` consumed one event iterator with no way to ask for the next prompt.
    ``multi_turn_events`` and ``on_submit`` removed that, and a default surface with no
    history, no slash commands and no rendering was the single biggest daily friction
    in the session audit — so the default follows the better surface.

    The REPL is still the answer for every case the TUI cannot serve: a pipe or any
    other non-tty, an install without the ``tui`` extra, and ``--no-tui`` for anyone who
    prefers it. None of those is a degraded TUI; each is the surface that actually works.
    """
    if options.tui and streams.isatty:
        if not textual_available():
            streams.err(TEXTUAL_MISSING + "\n")
        else:
            return await run_app(_app_session(options, agent, handoff))
    return await _repl(options, agent, streams)


def _app_session(options: Options, agent: Agent, handoff: Handoff | None) -> AppSession:
    """Everything the Textual view needs, wired to the live session.

    This function is the fix for a real defect rather than a tidy-up: the app has always
    accepted these callbacks and this call site has always omitted them, so ``esc`` and
    ``shift+tab`` moved the status line and nothing else, and an approval could be read
    but never answered. Each one below is a key that now reaches the thing it names.

    ``on_submit`` makes the view multi-turn. The argv prompt runs the first turn, exactly
    as before; the input line then feeds each follow-up onto a queue, and
    ``multi_turn_events`` runs it as the next turn on the same conversation (``agent.stream``
    continues it). The entry condition is unchanged — the TUI still starts from an argv
    prompt — so this adds follow-ups without deciding the separate "start with an empty
    TUI" question. ``ctrl+c`` still quits, cancelling the worker that awaits the queue.

    ``on_rewind`` is ``esc esc``, unified with ``/undo``. One turn back per double-press,
    destructive: :meth:`~ronin.cli.stream.Conversation.rewind` truncates the transcript
    to before the turn *and* restores that turn's checkpoint through the same
    ``CheckpointStore`` ``/undo`` uses, so the conversation and the work tree move
    together. It degrades cleanly — a read-only turn rewinds the conversation only, and a
    mutating turn with no git rewinds the conversation and says the files remain — and the
    one-line outcome is returned for the TUI to show. The ``index`` the app computes is
    advisory: v1 always steps one turn back rather than seeking to an arbitrary one.
    """
    policy = agent.runtime.policy
    submissions: asyncio.Queue[str | None] = asyncio.Queue()

    def run_turn(prompt: str) -> AsyncIterator[Event]:
        return agent.stream(
            prompt,
            budget=options.budget,
            max_iterations=options.max_iterations,
            verify=options.verify,
        )

    async def on_rewind(index: int) -> str:
        del index  # advisory; v1 rewinds exactly one turn (see the docstring)
        outcome = await agent.conversation.rewind(agent.runtime)
        return outcome.detail

    async def on_command(line: str) -> str:
        """Run one slash command for the TUI and hand back what it printed.

        ``_slash`` already writes through :class:`Streams`, so the whole adaptation is a
        Streams whose ``out``/``err`` append to a list instead of touching ``sys``. That
        is why the TUI gets the *real* command set — builtins, user-defined commands from
        ``.ronin/commands`` and skills — rather than a second implementation that would
        drift from the REPL's.

        A command that resolves to a prompt (a user-defined command, a skill) is queued
        as an ordinary turn, exactly as the REPL runs it: this function answers, it does
        not run turns. ``ask`` refuses rather than blocking — nothing in the TUI can
        answer a readline, and hanging the worker would freeze the screen.
        """
        captured: list[str] = []

        def refuse(_question: str) -> str:
            return ""

        result = await _slash(
            line,
            agent,
            Streams(
                out=captured.append,
                err=captured.append,
                ask=refuse,
                flush=lambda: None,
                isatty=False,
            ),
        )
        if result.prompt:
            submissions.put_nowait(result.prompt)
        if result.leave:
            captured.append(TUI_QUIT_NOTE)
        return "".join(captured).rstrip("\n")

    return AppSession(
        events=multi_turn_events(options.prompt, submissions, run_turn),
        model=agent.runtime.session.router.spec_for(ModelRole.MAIN).model,
        cwd=str(agent.loaded.paths.cwd),
        branch=current_branch(agent.loaded.paths.workspace_root),
        mode=agent.loaded.mode,
        on_interrupt=policy.cancel,
        on_rewind=on_rewind,
        on_mode_change=policy.set_mode,
        on_attach=handoff.attach if handoff is not None else None,
        on_status=lambda: context_share(agent.conversation.messages, agent.runtime.compaction),
        on_submit=submissions.put_nowait,
        on_command=on_command,
    )


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
            outcome = await _slash(request, agent, streams)
            if outcome.leave:
                return exit_code
            if not outcome.prompt:
                continue
            # A user-defined command expanded to a request. It runs as an ordinary turn —
            # same budget, same verification, same transcript — because that is what it is.
            request = outcome.prompt
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


async def _slash(line: str, agent: Agent, streams: Streams) -> Slash:
    """Run one slash command, and say what the session should do next.

    Dispatch is against the *workspace's* registry, not the builtin one. That is the whole
    of the user-command feature: ``.ronin/commands/review.md`` has always been parsed,
    validated and loaded onto ``Loaded.commands`` at startup, and this function used to
    hand ``BUILTIN_REGISTRY`` to the parser — so a command a user had written was
    unreachable from the only place anyone could type it, and answered "unknown command".
    """
    registry = agent.loaded.commands
    parsed = parse_command(line, registry=registry)
    if isinstance(parsed, ParseError):
        # Only reached when neither a builtin nor a user command owns the name, so a
        # skill can never shadow /clear or /plan: those parse successfully above. A skill
        # invoked this way runs its body as an ordinary turn, exactly like a user command
        # — the other half of "the tool and the slash command do the same thing".
        invocation = agent.loaded.skills.invocation(line)
        if invocation is not None:
            streams.out(f"/{invocation.skill.name} → skill ({invocation.skill.source})\n")
            return Slash(prompt=invocation.prompt)
        streams.err(parsed.display + "\n")
        return Slash()
    if parsed.is_user_defined:
        # A user command is a *prompt*, not an action: the file's body, with its arguments
        # substituted, is what the model should be asked. Running it here would mean this
        # function needed the budget, the iteration cap and the verify flag — so it is
        # handed back to the session, which owns turns.
        streams.out(f"/{parsed.name} → {parsed.source}\n")
        return Slash(prompt=parsed.body)
    name = parsed.name
    if name == "help":
        streams.out(render_help(registry) + "\n")
        skills = agent.loaded.skills
        if skills.skills:
            streams.out("\nskills (invoke with /name or the skill tool):\n")
            for skill in skills.skills:
                streams.out(f"  /{skill.name:<16} {skill.description}\n")
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
    elif name == "compact":
        await _compact_command(agent, streams)
    elif name == "model":
        _model_command(parsed.argument, agent, streams)
    elif name == "plan":
        _plan_command(agent, streams)
    elif name == "resume":
        _resume_command(parsed.argument, agent, streams)
    elif name == "agents":
        streams.out(_render_agents(agent))
    elif name == "hooks":
        streams.out(_render_hooks(agent))
    elif name == "init":
        # /init scaffolds the workspace; a running session already has a model, so it
        # does not re-detect or write a models.toml — that is the fresh-install path.
        await _first_run(agent.loaded.paths, streams)
    else:  # pragma: no cover - every declared command above is wired
        streams.err(
            f"/{name} is a real command but is not wired into this line session. "
            f"`{PROGRAM} --help` lists what is.\n"
        )
    return Slash()


async def _compact_command(agent: Agent, streams: Streams) -> None:
    """Fold the transcript now, and report what it bought.

    Both numbers, never just the new one: "23,000 tokens" alone cannot tell you whether
    compacting helped, and the ratio is the only honest measure of that.
    """
    try:
        result = await agent.conversation.compact_now(agent.runtime)
    except CompactionPairingError as exc:
        streams.err(f"cannot compact: {exc}\n")
        return
    if not result.compacted:
        streams.out("nothing to compact: the transcript is shorter than the fold needs.\n")
        return
    kept = (
        f", keeping {len(result.retained_paths)} file result(s) in full"
        if result.retained_paths
        else ""
    )
    streams.out(
        f"folded {result.folded_messages} message(s): ~{result.token_estimate_before:,} → "
        f"~{result.token_estimate_after:,} tokens{kept}\n"
    )
    for note in agent.conversation.notes[-2:]:
        streams.err(f"note: {note}\n")


def _model_command(argument: str, agent: Agent, streams: Streams) -> None:
    """Show the models, or switch the one that answers the next turn."""
    router = agent.runtime.session.router
    if not argument:
        active = agent.runtime.session.router.spec_for(ModelRole.MAIN).model
        streams.out(f"main model: {active}\n")
        streams.out(f"configured: {', '.join(router.model_names) or 'none'}\n")
        streams.out(f"switch with `/model <name>`; {PROGRAM} --model sets it at startup.\n")
        return
    try:
        now = agent.use_model(argument)
    except KeyError:
        streams.err(
            f"no model called {argument!r} is configured. "
            f"known: {', '.join(router.model_names) or 'none'}\n"
        )
        return
    streams.out(f"next turn runs on {now}. subagents and compaction are unchanged.\n")


def _plan_command(agent: Agent, streams: Streams) -> None:
    """Enter plan mode. One-way, and it says so rather than implying otherwise."""
    if agent.loaded.mode is Mode.PLAN:
        streams.out("already in plan mode: no tool that can mutate is on the table.\n")
        return
    try:
        agent.enter_plan_mode()
    except ValueError as exc:
        # `plan_runtime` refuses to build a registry with nothing in it, and it is right
        # to: a session with no read-only tool cannot plan, and pretending otherwise would
        # leave the model with no way to look at anything.
        streams.err(f"cannot enter plan mode: {exc}\n")
        return
    tools = ", ".join(sorted(spec.name for spec in agent.runtime.registry.specs()))
    streams.out(f"plan mode. the model now has: {tools}\n")
    streams.out("this is not reversible in this session — restart to get the write tools back.\n")


def _resume_command(argument: str, agent: Agent, streams: Streams) -> None:
    """Seed the conversation from a recorded session, mid-session.

    The same replay ``--resume`` uses, so a resumed transcript carries the same caveats:
    a session with three synthesized tool results is not the same thing as a clean one,
    and a user who is not told reads the difference as the model forgetting.
    """
    directory = agent.loaded.paths.sessions_dir
    try:
        found = (
            resume_session(directory, argument)
            if argument
            else resume_latest(directory, str(agent.loaded.paths.cwd))
        )
    except (OSError, RuntimeError) as exc:
        streams.err(f"cannot resume: {exc}\n")
        return
    if found is None:
        streams.err(f"no recorded session here to continue. `{PROGRAM} sessions` lists what is.\n")
        return
    agent.conversation.resume_from(found.state)
    streams.out(
        f"resumed {found.meta.session_id}: {len(found.replay.turns)} turn(s), "
        f"{len(found.state.messages)} message(s)\n"
    )
    for caveat in (*found.skipped, *found.replay.repairs, *found.replay.notes):
        streams.err(f"note: {caveat}\n")


def _render_agents(agent: Agent) -> str:
    """The subagents this workspace can delegate to, and what each is for."""
    definitions = agent.loaded.agents
    if not definitions:
        return "no subagents are defined. add one as .ronin/agents/<name>.md\n"
    lines = [f"{len(definitions)} subagent(s):"]
    for name in sorted(definitions):
        definition = definitions[name]
        tools = ", ".join(definition.tools) if definition.tools else "every tool"
        lines.append(f"  {name} [{definition.model}] — {definition.description}")
        lines.append(f"    tools: {tools}")
    return "\n".join(lines) + "\n"


def _render_hooks(agent: Agent) -> str:
    """The configured hooks, grouped by the event that fires them.

    Grouped rather than listed flat because the question a human has is "what runs when I
    edit a file", and a flat list of commands makes them answer it by eye.
    """
    hooks = agent.loaded.hooks.hooks
    if not hooks:
        return "no hooks are configured. add them in .ronin/hooks.json\n"
    lines = [f"{len(hooks)} hook(s):"]
    for event in sorted({spec.event for spec in hooks}, key=str):
        lines.append(f"  {event}:")
        for spec in (spec for spec in hooks if spec.event is event):
            matcher = "" if spec.matcher == MATCH_ALL else f" [{spec.matcher}]"
            lines.append(f"    {spec.command}{matcher}")
    return "\n".join(lines) + "\n"


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
