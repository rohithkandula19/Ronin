"""``ronin eval`` and ``ronin duel``: the eval suite, reachable from a command line.

Everything under :mod:`ronin.evals` was complete, tested, and **unreachable** — 118
verified fixtures, a runner, a six-class taxonomy, a paired A/B, and no way to invoke
any of it except by importing the package in a REPL. An eval suite nobody can run is a
directory of good intentions, so this module is the argv-to-behaviour half.

Three properties are deliberate.

**``--dry-run`` needs no model, no key, and no network.** It loads the suite, applies
the selection, and prints exactly what *would* run. That is the difference between a
command a first-time reader can try and a command that demands an API key before it
will tell them anything — and it is the only form of these commands CI can exercise,
since the suite is required to work offline with no credentials.

**``--model`` names a model your router config defines**, and a wrong name lists the
ones that exist. It does not accept a provider's model id: resolving one would mean
inventing a price, and a made-up price becomes a wrong number in a cost report. The
named spec is promoted to the ``main`` role for the run, so the rest of the stack is
unchanged from an ordinary session.

**Nothing here computes a score.** :mod:`ronin.evals.report` does that; this module
selects tasks, builds a router, and writes bytes. A command that both ran the suite
and decided what "passed" means is a command that can quietly redefine the number.
"""
from __future__ import annotations

import functools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final

from ..evals import (
    SUITE_RELATIVE_ROOT,
    EvalTask,
    RunnerConfig,
    RunReport,
    SuiteSelection,
    TaskError,
    load_tasks,
    report_json,
    report_markdown,
    run_suite,
    sdk_agent_factory,
    v2_adapter,
)
from ..evals.adapters import AgentFactory
from ..evals.duel import (
    DEFAULT_MAX_DIFF_LINES,
    DuelistFactory,
    duel_scoreboard,
    render_duel_markdown,
    render_duel_scoreboard,
    run_duel,
    suite_runner,
)
from ..evals.task import load_manifest
from ..providers.router import Role, Router
from ..providers.types import ProviderError
from .sdk import load_router
from .spine import Paths

#: Both commands exit 1 when the suite could not even be selected — a missing suite,
#: an unknown model, a selection matching nothing. Distinct from "tasks failed", which
#: is a *result* and exits 0: a run that measured a 40% pass rate did its job.
EXIT_OK: Final = 0
EXIT_ERROR: Final = 1

#: How many tasks ``--dry-run`` lists before summarising. Long enough to see the shape
#: of a category, short enough that ``--dry-run`` over the whole suite is readable.
DRY_RUN_LIST_LIMIT: Final = 40


@dataclass(frozen=True, slots=True)
class BenchOptions:
    """What ``eval``/``duel`` were asked to do. Built by ``main``, which owns argv.

    argv parsing lives in :mod:`ronin.cli.main` — "the whole argv surface, in one place
    so ``--help`` cannot drift from it" — so this module never sees a string it has to
    interpret, and every field below is already the type it means.
    """

    suite: Path
    models: tuple[str, ...] = ()
    parallel: int = 1
    ids: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()
    regression_gate: bool = False
    limit: int | None = None
    seed: int = 0
    json_out: Path | None = None
    markdown_out: Path | None = None
    workspace_root: Path | None = None
    keep_workspaces: bool = False
    record: bool = False
    allow_network: bool = False
    dry_run: bool = False
    max_diff_lines: int = DEFAULT_MAX_DIFF_LINES
    #: Injected by tests so the whole command is exercisable without a model. Not a
    #: user-facing flag: there is no argv spelling for it, on purpose.
    router: Router | None = field(default=None, compare=False)

    @property
    def selection(self) -> SuiteSelection:
        return SuiteSelection(
            ids=self.ids,
            categories=self.categories,
            tags=self.tags,
            regression_gate_only=self.regression_gate,
            limit=self.limit,
        )


@dataclass(frozen=True, slots=True)
class Failed:
    """Why the command cannot proceed, as a value rather than an exception.

    Errors are values across tool boundaries in this codebase, and a CLI entry point
    is the same kind of boundary: a caller that has to catch three exception types to
    print one message is a caller that will eventually catch the wrong one.
    """

    message: str


# --------------------------------------------------------------------------- #
# Selecting
# --------------------------------------------------------------------------- #


def default_suite(paths: Paths) -> Path:
    """``tests/evals`` under the workspace root. A default, never a hard-coding."""
    return paths.workspace_root / SUITE_RELATIVE_ROOT


def select(options: BenchOptions) -> tuple[EvalTask, ...] | Failed:
    """Load the suite and apply the selection, naming what went wrong if it did.

    The manifest check is *reported*, not enforced: a contributor mid-way through
    adding fixtures should still be able to run the ones that exist. Refusing here
    would make the manifest a gate on experimentation rather than what it is — the
    thing that makes a deleted fixture detectable in CI.
    """
    if not options.suite.is_dir():
        return Failed(
            f"no eval suite at {options.suite} — pass --suite PATH, or run from a "
            f"checkout that has {SUITE_RELATIVE_ROOT}/"
        )
    try:
        tasks = load_tasks(options.suite)
    except TaskError as exc:
        return Failed(str(exc))
    if not tasks:
        return Failed(f"{options.suite} holds no task.toml")

    chosen = options.selection.apply(tasks)
    if not chosen:
        return Failed(
            f"the selection matched none of the {len(tasks)} tasks in "
            f"{options.suite}. {_selection_summary(options)}"
        )
    return chosen


def _selection_summary(options: BenchOptions) -> str:
    """What was asked for, so an empty result explains itself."""
    parts: list[str] = []
    if options.ids:
        parts.append(f"ids={sorted(options.ids)}")
    if options.categories:
        parts.append(f"categories={sorted(options.categories)}")
    if options.tags:
        parts.append(f"tags={sorted(options.tags)}")
    if options.regression_gate:
        parts.append("regression-gate only")
    if options.limit is not None:
        parts.append(f"limit={options.limit}")
    return ("asked for: " + ", ".join(parts)) if parts else "no filters were given"


def manifest_note(options: BenchOptions, tasks: Sequence[EvalTask]) -> str:
    """A warning when the manifest and the tree disagree, or "" when they agree.

    Surfaced on stderr rather than raised (see :func:`select`). Worth saying out loud
    even on a filtered run, because "the suite got smaller and nobody noticed" is the
    failure the manifest exists to catch.
    """
    try:
        manifest = load_manifest(options.suite)
    except (FileNotFoundError, TaskError) as exc:
        return f"note: {exc}"
    declared = set(manifest.task_ids)
    try:
        on_disk = {task.id for task in load_tasks(options.suite)}
    except TaskError:
        return ""
    missing_from_manifest = sorted(on_disk - declared)
    missing_from_disk = sorted(declared - on_disk)
    if not missing_from_manifest and not missing_from_disk:
        return ""
    parts = [f"note: {manifest.path.name} disagrees with the tree"]
    if missing_from_manifest:
        parts.append(f"on disk but unlisted: {', '.join(missing_from_manifest[:5])}")
    if missing_from_disk:
        parts.append(f"listed but absent: {', '.join(missing_from_disk[:5])}")
    parts.append("run `python scripts/gen_eval_manifest.py`")
    return "; ".join(parts)


def render_dry_run(tasks: Sequence[EvalTask], options: BenchOptions) -> str:
    """What would run, and what it would cost in turns. No model, no key, no network."""
    lines = [
        f"{len(tasks)} task(s) selected from {options.suite}",
        f"parallel={options.parallel}  "
        f"models={', '.join(options.models) if options.models else '(none given)'}",
        "",
    ]
    by_category: dict[str, int] = {}
    for task in tasks:
        by_category[task.category] = by_category.get(task.category, 0) + 1
    for category in sorted(by_category):
        lines.append(f"  {category:24} {by_category[category]}")
    lines.append("")

    shown = list(tasks[:DRY_RUN_LIST_LIMIT])
    for task in shown:
        gate = " [gate]" if task.regression_gate else ""
        lines.append(f"  {task.id}{gate}  max_turns={task.max_turns}")
    if len(tasks) > len(shown):
        lines.append(f"  ... and {len(tasks) - len(shown)} more")

    turns = sum(task.max_turns for task in tasks)
    lines.extend(
        [
            "",
            f"turn ceiling if every task ran to its limit: {turns}. That is a ceiling, "
            "not an estimate — the runner records what was actually used.",
            "nothing ran: --dry-run does not open a model.",
        ]
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# The router
# --------------------------------------------------------------------------- #


def router_for(
    options: BenchOptions,
    paths: Paths,
    environ: Mapping[str, str],
    model: str,
) -> Router | Failed:
    """A router whose ``main`` role is ``model``, or why one could not be built.

    ``model`` names an entry in the router config's ``[models]`` table, not a
    provider's model id. Accepting a raw id would mean this function inventing a
    price per million tokens for it, and an invented price is a wrong number in
    someone's cost report — so an unknown name lists the defined ones instead.
    """
    if options.router is not None:
        return options.router
    try:
        router = load_router(paths, environ=environ)
    except FileNotFoundError as exc:
        return Failed(str(exc))
    except ProviderError as exc:
        return Failed(f"router config is unusable: {exc}")

    if not model:
        return router
    config = router.config
    if model not in config.models:
        return Failed(
            f"--model {model!r} is not defined in your router config. "
            f"Defined: {', '.join(sorted(config.models))}. "
            "The value names a model in the config's [models] table, not a "
            "provider's model id."
        )
    repointed = replace(config, roles={**config.roles, Role.MAIN: model})
    return Router(repointed, env=environ)


# --------------------------------------------------------------------------- #
# eval
# --------------------------------------------------------------------------- #


def runner_config(options: BenchOptions, model: str) -> RunnerConfig:
    return RunnerConfig(
        parallel=options.parallel,
        workspace_root=options.workspace_root,
        keep_workspaces=options.keep_workspaces,
        allow_network=options.allow_network,
        model=model,
        label=model or "unlabelled",
        suite_root=str(options.suite),
    )


async def run_eval(
    options: BenchOptions,
    paths: Paths,
    environ: Mapping[str, str],
    *,
    factory: AgentFactory | None = None,
) -> tuple[int, str, str]:
    """Run the suite once. Returns ``(exit_code, stdout, stderr)``.

    Returning text rather than printing keeps this callable from a test without
    capturing a stream, which is the same reason ``main`` injects its ``Streams``.

    ``factory`` replaces :func:`sdk_agent_factory`, and it is the seam that makes the
    *running* path testable at all: injecting a router is not enough, because the
    default factory opens a real agent against a real provider. With it, the whole
    command — selection, config, report, file writes, exit code — is exercised offline
    against a scripted model. Without that seam the only testable branch would be
    ``--dry-run``, and the branch that matters would ship unverified.
    """
    chosen = select(options)
    if isinstance(chosen, Failed):
        return EXIT_ERROR, "", chosen.message + "\n"

    notes = manifest_note(options, chosen)
    if options.dry_run:
        return EXIT_OK, render_dry_run(chosen, options), _err(notes)

    if len(options.models) > 1:
        return EXIT_ERROR, "", (
            "eval runs one model; you gave "
            f"{len(options.models)}. Use `ronin duel` to compare two.\n"
        )
    model = options.models[0] if options.models else ""
    open_agent = factory
    if open_agent is None:
        router = router_for(options, paths, environ, model)
        if isinstance(router, Failed):
            return EXIT_ERROR, "", router.message + "\n"
        open_agent = functools.partial(
            sdk_agent_factory, router=router, connect_mcp=False, record=options.record
        )
    report = await run_suite(
        chosen, v2_adapter(open_agent), config=runner_config(options, model)
    )
    written = _write_outputs(options, report)
    return EXIT_OK, report_markdown(report), _err(notes, *written)


def _write_outputs(options: BenchOptions, report: RunReport) -> list[str]:
    """Write ``--json``/``--markdown`` if asked, and say where each landed."""
    written: list[str] = []
    if options.json_out is not None:
        options.json_out.parent.mkdir(parents=True, exist_ok=True)
        options.json_out.write_text(report_json(report), encoding="utf-8")
        written.append(f"wrote {options.json_out}")
    if options.markdown_out is not None:
        options.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        options.markdown_out.write_text(report_markdown(report), encoding="utf-8")
        written.append(f"wrote {options.markdown_out}")
    return written


def _err(*lines: str) -> str:
    """Join the non-empty notes into a stderr blob."""
    kept = [line for line in lines if line]
    return "\n".join(kept) + "\n" if kept else ""


# --------------------------------------------------------------------------- #
# duel
# --------------------------------------------------------------------------- #


async def run_duel_command(
    options: BenchOptions,
    paths: Paths,
    environ: Mapping[str, str],
    *,
    factory: DuelistFactory | None = None,
) -> tuple[int, str, str]:
    """Run the same tasks against two models at the same per-task seeds.

    Two routers rather than one with two roles: a duel where the sides share a role
    is a duel where one side's fallback config can silently change the other's, and
    the whole point is that the only difference between the sides is the model.

    ``factory`` is the same injection seam as :func:`run_eval`'s — see there.
    """
    chosen = select(options)
    if isinstance(chosen, Failed):
        return EXIT_ERROR, "", chosen.message + "\n"

    notes = manifest_note(options, chosen)
    if options.dry_run:
        return EXIT_OK, render_dry_run(chosen, options), _err(notes)

    if len(options.models) != 2:
        return EXIT_ERROR, "", (
            f"duel compares exactly two models; you gave {len(options.models)}. "
            "Pass --model twice.\n"
        )
    a_name, b_name = options.models
    if a_name == b_name:
        return EXIT_ERROR, "", (
            f"both sides are {a_name!r}. A duel against itself measures sampling "
            "noise, which is a real thing to measure but not with this command.\n"
        )

    duelists: DuelistFactory | None = factory
    if duelists is None:
        routers: dict[str, Router] = {}
        for name in (a_name, b_name):
            built = router_for(options, paths, environ, name)
            if isinstance(built, Failed):
                return EXIT_ERROR, "", built.message + "\n"
            routers[name] = built

        def from_router(duelist: str, seed: int) -> AgentFactory:
            # ``seed`` is per task, from ``task_seed``, and is *not* plumbed into
            # sampling here: the router owns temperature and this layer has no
            # honest way to set a provider's seed. It still determines which tasks
            # pair with which, which is what the duel's own reproducibility rests
            # on — so it is accepted rather than rejected, and dropping it silently
            # is the thing this comment exists to stop someone assuming.
            del seed
            return functools.partial(
                sdk_agent_factory,
                router=routers[duelist],
                connect_mcp=False,
                record=options.record,
            )

        duelists = from_router

    duel = await run_duel(
        chosen,
        factory=duelists,
        suite=suite_runner(config=runner_config(options, "")),
        duelists=(a_name, b_name),
        seed=options.seed,
        max_diff_lines=options.max_diff_lines,
    )
    board = duel_scoreboard(duel)
    rendered = render_duel_scoreboard(board)

    written: list[str] = []
    if options.markdown_out is not None:
        options.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        options.markdown_out.write_text(render_duel_markdown(duel), encoding="utf-8")
        written.append(f"wrote {options.markdown_out}")
    if options.json_out is not None:
        options.json_out.parent.mkdir(parents=True, exist_ok=True)
        options.json_out.write_text(
            json.dumps(
                {
                    "seed": duel.seed,
                    "duelists": list(duel.duelists),
                    "incomparable": list(duel.incomparable),
                    "reports": [
                        json.loads(report_json(report)) for report in duel.reports
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        written.append(f"wrote {options.json_out}")

    return EXIT_OK, rendered, _err(notes, *written)


__all__ = [
    "DRY_RUN_LIST_LIMIT",
    "EXIT_ERROR",
    "EXIT_OK",
    "BenchOptions",
    "Failed",
    "default_suite",
    "manifest_note",
    "render_dry_run",
    "router_for",
    "run_duel_command",
    "run_eval",
    "runner_config",
    "select",
]
