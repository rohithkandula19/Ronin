"""``ronin harvest``: run a task pool, record trajectories, emit training datasets.

The trajectory flywheel's executor. Everything under :mod:`ronin.evals.harvest` turns
recorded runs into SFT/DPO/RLVR datasets, but it *never runs a task* — recording one
needs a model, a workspace and a verify signal, which are the runner's job. This module
is the seam that joins the two: it selects a pool, runs it through the eval runner with
recording forced on, reads each task's transcript back paired with its verify result,
and hands the lot to :func:`ronin.evals.harvest.harvest`.

Three properties are deliberate, and each mirrors ``ronin eval`` (:mod:`ronin.cli.bench`)
because a second convention for the same shape is a second thing to learn.

**``--dry-run`` needs no model, no key, and no network.** It loads the pool, applies the
selection, and prints what *would* run and where the datasets would land. It is the only
form CI can exercise without a provider, and the demo a first reader can try.

**Recording is forced on, not offered.** A harvest whose runner did not record has
nothing to read — the measured yield is zero rows — so ``harvest`` sets ``record=True``
and keeps the workspaces alive across the run itself, rather than trusting the caller to
remember two flags whose absence fails silently.

**The eval suite is refused as input.** Harvesting ``tests/evals`` would turn the
benchmark into training data and destroy the one signal that is supposed to stay
independent of training. ``ronin.evals.poolgen`` already refuses to *write* a pool there;
this refuses to *read* one from there, for the same reason from the other side.

``--container`` runs each task's ``verify.sh`` in ``docker run --network=none`` via
:func:`ronin.evals.poolgen.container_command` — the same reproducible verify the pool was
generated against. It isolates the *verification*, not the agent's own tool calls, and it
fails closed when no docker binary is on ``PATH`` rather than mislabelling every task a
failure. The live path needs a daemon; the argv it builds is pure and unit-tested.
"""

from __future__ import annotations

import functools
import json
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from ..evals import (
    EvalTask,
    RunnerConfig,
    SuiteSelection,
    TaskError,
    load_tasks,
    sdk_agent_factory,
    v2_adapter,
)
from ..evals.adapters import AgentFactory
from ..evals.harvest import (
    HarvestResult,
    TaskOutcome,
    outcome_from_transcript,
    write_dataset,
)
from ..evals.poolgen import DEFAULT_IMAGE, container_command
from ..evals.runner import WORK_DIRNAME, EvalRunner
from ..evals.taxonomy import TaskResult
from ..persistence.transcript import (
    TranscriptError,
    list_sessions,
    read_events,
    session_path,
    sessions_dir,
)
from ..providers.router import Router
from ..verify.runner import DEFAULT_COMMAND_TIMEOUT, CommandOutcome, CommandRunner, run_command
from .bench import EXIT_ERROR, EXIT_OK, Failed, resolve_router
from .spine import Paths

#: How many tasks ``--dry-run`` lists before summarising, matching ``eval``.
DRY_RUN_LIST_LIMIT: Final = 40

#: The path segments that mark the eval suite. Harvesting from under here is refused —
#: see the module docstring. Kept local rather than imported so this guard does not
#: depend on poolgen's internal name staying public.
EVAL_SUITE_MARKER: Final[tuple[str, ...]] = ("tests", "evals")

#: The datasets a harvest writes, plus the tagging file. Named so the summary can say
#: exactly what landed and a test can assert the set.
SUMMARY_FILE: Final = "harvest_summary.json"


@dataclass(frozen=True, slots=True)
class HarvestOptions:
    """What ``harvest`` was asked to do. Built by :mod:`ronin.cli.main`, which owns argv."""

    tasks: Path
    out: Path
    models: tuple[str, ...] = ()
    parallel: int = 1
    ids: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()
    regression_gate: bool = False
    limit: int | None = None
    workspace_root: Path | None = None
    keep_workspaces: bool = False
    container: bool = False
    image: str = DEFAULT_IMAGE
    allow_network: bool = False
    dry_run: bool = False
    #: Injected by tests so the whole command runs without a model. No argv spelling.
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


# --------------------------------------------------------------------------- #
# Selecting the pool
# --------------------------------------------------------------------------- #


def is_eval_suite(root: Path) -> bool:
    """Whether ``root`` is, or is under, ``tests/evals`` — the input harvest refuses."""
    parts = root.resolve().parts
    marker = EVAL_SUITE_MARKER
    return any(parts[i : i + len(marker)] == marker for i in range(len(parts)))


def select_pool(options: HarvestOptions) -> tuple[EvalTask, ...] | Failed:
    """Load the pool and apply the selection, naming what went wrong if it did.

    Unlike ``eval``'s :func:`ronin.cli.bench.select`, there is no manifest gate: a pool
    is a working set that grows, not a suite whose deletions must be caught. The one
    hard refusal is the eval suite itself — see the module docstring.
    """
    if is_eval_suite(options.tasks):
        return Failed(
            f"refusing to harvest {options.tasks}: that is the eval suite, and turning "
            "its trajectories into training data would destroy the benchmark's "
            "independence. Point --tasks at a pool built by `python -m "
            "ronin.evals.poolgen`."
        )
    if not options.tasks.is_dir():
        return Failed(f"no task pool at {options.tasks} — pass --tasks PATH to a pool of tasks")
    try:
        tasks = load_tasks(options.tasks)
    except TaskError as exc:
        return Failed(str(exc))
    if not tasks:
        return Failed(f"{options.tasks} holds no task.toml")

    chosen = options.selection.apply(tasks)
    if not chosen:
        return Failed(f"the selection matched none of the {len(tasks)} tasks in {options.tasks}")
    return chosen


def render_dry_run(tasks: Sequence[EvalTask], options: HarvestOptions) -> str:
    """What would run and where the datasets would land. No model, no key, no network."""
    lines = [
        f"{len(tasks)} task(s) selected from {options.tasks}",
        f"parallel={options.parallel}  "
        f"model={options.models[0] if options.models else '(none given)'}  "
        f"container={'on' if options.container else 'off'}",
        f"datasets would be written to {options.out}/ (sft.jsonl, dpo.jsonl, rlvr.jsonl)",
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
        lines.append(f"  {task.id}  max_turns={task.max_turns}")
    if len(tasks) > len(shown):
        lines.append(f"  ... and {len(tasks) - len(shown)} more")

    lines.extend(
        [
            "",
            "each task would be recorded (record=True) and its transcript harvested into "
            "SFT/DPO/RLVR records.",
            "nothing ran: --dry-run does not open a model.",
        ]
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Containerised verify
# --------------------------------------------------------------------------- #


def container_command_runner(base: CommandRunner, *, image: str) -> CommandRunner:
    """Wrap a command runner so ``verify.sh`` runs in a container instead of on the host.

    The eval runner invokes ``[bash, verify.sh]`` with ``cwd`` set to the workspace; this
    rewrites that into the ``docker run --network=none`` argv
    :func:`ronin.evals.poolgen.container_command` builds — one source of truth for the
    container contract — mounting the workspace read-write and running the same script.
    """

    async def run(
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float = DEFAULT_COMMAND_TIMEOUT,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        verify_name = argv[-1] if argv else "verify.sh"
        wrapped = container_command(Path(cwd), image=image, verify_name=verify_name, network=False)
        return await base(wrapped, cwd=Path(cwd), timeout=timeout, env=env)

    return run


# --------------------------------------------------------------------------- #
# Reading transcripts back into harvest inputs
# --------------------------------------------------------------------------- #


def outcomes_from_results(results: Sequence[TaskResult], *, notes: list[str]) -> list[TaskOutcome]:
    """Turn finished runs into :class:`TaskOutcome`s by reading each one's transcript.

    Every task the runner finished carries its workspace path and verify result on
    ``TaskResult.record``; the transcript lives under ``<workspace>/work/.ronin/sessions``
    because the agent ran with that as its cwd. A run with no readable transcript — a
    skip, a crash before the first turn, a torn version — is recorded as a note and
    dropped, never raised: one unrecordable task must not cost the rest their dataset.
    """
    outcomes: list[TaskOutcome] = []
    for result in sorted(results, key=lambda item: item.task_id):
        record = result.record
        if record.skipped:
            notes.append(f"{record.task_id}: skipped ({record.skip_reason or 'no reason given'})")
            continue
        if not record.workspace:
            notes.append(f"{record.task_id}: the runner recorded no workspace")
            continue
        directory = sessions_dir(Path(record.workspace) / WORK_DIRNAME)
        metas = list_sessions(directory) if directory.is_dir() else ()
        if not metas:
            notes.append(
                f"{record.task_id}: no transcript recorded — harvest forces record=True, "
                "so this is an empty run rather than a missing flag"
            )
            continue
        transcript = session_path(directory, metas[0].session_id)
        try:
            read = read_events(transcript)
        except TranscriptError as exc:
            notes.append(f"{record.task_id}: unreadable transcript ({exc})")
            continue
        outcomes.append(
            outcome_from_transcript(
                read,
                task_id=record.task_id,
                verify_passed=record.verify_after.passed,
                category=record.category,
                prompt=record.prompt,
                target_paths=record.files_expected,
                registered_tools=record.outcome.registered_tools,
            )
        )
    return outcomes


# --------------------------------------------------------------------------- #
# The tagging summary
# --------------------------------------------------------------------------- #


def harvest_summary(result: HarvestResult, notes: Sequence[str]) -> dict[str, Any]:
    """A machine-readable account of what every input became — the tagging layer.

    Every source task is tagged with its disposition: ``accepted`` into SFT, ``rejected``
    with the gate that dropped it and its taxonomy class, or ``skipped`` for a run that
    could not be read. Deterministic (sorted keys, sorted lists) so two harvests of the
    same pool diff cleanly.
    """
    tags: dict[str, dict[str, str]] = {}
    for task_id in result.accepted:
        tags[task_id] = {"disposition": "accepted"}
    for rejection in result.rejections:
        tags[rejection.task_id] = {
            "disposition": "rejected",
            "reason": rejection.reason,
            "failure_class": rejection.failure_class,
        }
    for skip in result.skips:
        tags[skip.task_id or "<no id>"] = {"disposition": "skipped", "reason": skip.reason}
    return {
        "counts": {
            "accepted": result.accepted_count,
            "rejected": result.rejected_count,
            "skipped": result.skipped_count,
            "sft": len(result.sft),
            "dpo": len(result.dpo),
            "rlvr": len(result.rlvr),
        },
        "rejection_reasons": result.rejection_reasons(),
        "category_medians": dict(sorted(result.category_medians.items())),
        "tags": dict(sorted(tags.items())),
        "notes": list(notes),
    }


def render_summary(result: HarvestResult, notes: Sequence[str], out: Path) -> str:
    """The human-facing summary printed after a harvest."""
    lines = [
        f"harvested {result.accepted_count + result.rejected_count + result.skipped_count} run(s)",
        f"  accepted (SFT): {result.accepted_count}",
        f"  rejected:       {result.rejected_count}",
        f"  skipped:        {result.skipped_count}",
        "",
        f"datasets: {len(result.sft)} sft, {len(result.dpo)} dpo, {len(result.rlvr)} rlvr rows",
    ]
    reasons = result.rejection_reasons()
    if reasons:
        lines.append("rejections by reason:")
        for reason, count in sorted(reasons.items()):
            lines.append(f"  {count:4}  {reason}")
    lines.append(f"wrote {out}/ (sft.jsonl, dpo.jsonl, rlvr.jsonl, {SUMMARY_FILE})")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


async def _harvest_run(
    options: HarvestOptions,
    tasks: Sequence[EvalTask],
    run_agent: AgentFactory,
    *,
    command_runner: CommandRunner = run_command,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[HarvestResult, list[str]]:
    """Run the pool with recording forced on, then harvest the transcripts it left.

    Workspaces are kept for the duration of the run whatever the caller asked, because a
    deleted workspace is a deleted transcript and harvest would read nothing. A temp root
    the run owns is removed afterwards unless ``--keep-workspaces`` was passed; a root the
    caller named with ``--workspaces`` is always left alone.
    """
    notes: list[str] = []
    owned: str | None = None
    root = options.workspace_root
    if root is None:
        owned = tempfile.mkdtemp(prefix="ronin-harvest-")
        root = Path(owned)

    runner_command = (
        container_command_runner(command_runner, image=options.image)
        if options.container
        else command_runner
    )
    config = RunnerConfig(
        parallel=options.parallel,
        workspace_root=root,
        keep_workspaces=True,  # forced: the transcript must survive the run to be read
        allow_network=options.allow_network,
        model=options.models[0] if options.models else "",
        label=(options.models[0] if options.models else "unlabelled"),
        suite_root=str(options.tasks),
    )
    runner = EvalRunner(
        v2_adapter(run_agent), config=config, command_runner=runner_command, clock=clock
    )
    try:
        await runner.run(tasks)
        outcomes = outcomes_from_results(runner.results, notes=notes)
        result = harvest_outcomes(outcomes)
    finally:
        if owned is not None and not options.keep_workspaces:
            shutil.rmtree(owned, ignore_errors=True)
    return result, notes


def harvest_outcomes(outcomes: Sequence[TaskOutcome]) -> HarvestResult:
    """Run the offline harvest over folded outcomes. Split out for a direct unit test."""
    from ..evals.harvest import harvest

    return harvest(outcomes)


def _write_outputs(options: HarvestOptions, result: HarvestResult, notes: Sequence[str]) -> None:
    """Write the three datasets and the tagging summary under ``--out``."""
    options.out.mkdir(parents=True, exist_ok=True)
    write_dataset(result, options.out)
    summary = harvest_summary(result, notes)
    (options.out / SUMMARY_FILE).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


async def run_harvest(
    options: HarvestOptions,
    paths: Paths,
    environ: Mapping[str, str],
    *,
    factory: AgentFactory | None = None,
) -> tuple[int, str, str]:
    """Run one harvest. Returns ``(exit_code, stdout, stderr)``.

    ``factory`` replaces :func:`ronin.evals.sdk_agent_factory` — the seam that makes the
    running path testable offline against a scripted model, exactly as ``run_eval`` uses
    it. Without it the only exercisable branch would be ``--dry-run``.
    """
    chosen = select_pool(options)
    if isinstance(chosen, Failed):
        return EXIT_ERROR, "", chosen.message + "\n"

    if options.dry_run:
        return EXIT_OK, render_dry_run(chosen, options), ""

    if options.container and factory is None and shutil.which("docker") is None:
        return (
            EXIT_ERROR,
            "",
            "--container needs docker on PATH; none was found. Run without --container to "
            "verify on the host, or install docker.\n",
        )
    if len(options.models) > 1:
        return EXIT_ERROR, "", f"harvest runs one model; you gave {len(options.models)}.\n"

    model = options.models[0] if options.models else ""
    open_agent = factory
    if open_agent is None:
        router = resolve_router(options.router, paths, environ, model)
        if isinstance(router, Failed):
            return EXIT_ERROR, "", router.message + "\n"
        open_agent = functools.partial(
            sdk_agent_factory, router=router, connect_mcp=False, record=True
        )

    result, notes = await _harvest_run(options, chosen, open_agent)
    _write_outputs(options, result, notes)
    return EXIT_OK, render_summary(result, notes, options.out), ""


__all__ = [
    "DRY_RUN_LIST_LIMIT",
    "EVAL_SUITE_MARKER",
    "SUMMARY_FILE",
    "HarvestOptions",
    "container_command_runner",
    "harvest_outcomes",
    "harvest_summary",
    "is_eval_suite",
    "outcomes_from_results",
    "render_dry_run",
    "render_summary",
    "run_harvest",
    "select_pool",
]
