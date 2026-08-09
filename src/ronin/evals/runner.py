"""The eval runner: ``ronin eval --model X --parallel 8``, as an async function.

What this module guarantees, in the order the guarantees matter.

**Every task produces a well-formed result.** A task that raises, times out, needs
the network, or is cancelled becomes a :class:`~ronin.evals.taxonomy.TaskResult` with
the reason recorded. Nothing here lets one bad fixture end a run over the other
fifty-nine, because a suite that dies on task 7 is a suite people stop running.

**Isolation is per task, and the answer key never reaches it.** Each task gets its own
temp directory holding two copies of the fixture: ``work/`` for the agent and
``baseline/`` for the pre-run probe. Both are produced by
:func:`ronin.evals.task.materialize`, which refuses to copy ``solution/``.

**The baseline is taken before the agent runs.** ``BROKE_TESTS`` is the difference
between "this was already failing" and "the agent broke it", and that difference does
not exist without a pre-run probe. It runs on the untouched ``baseline/`` copy rather
than on ``work/`` before handing it over, so no ordering bug can let the baseline see
the agent's edits.

**The model is injected.** The runner takes a :data:`~ronin.evals.adapters.RunAgent`
and never constructs a provider client. Determinism follows from that: with a scripted
fake and an injected clock, two runs produce byte-identical reports — asserted in the
tests, because "deterministic" is a property that decays the moment it is not checked.

**The concurrency cap is real.** One :class:`asyncio.Semaphore` sized by
``parallel``, and the observed peak is recorded on :attr:`EvalRunner.peak_concurrency`
so the cap is testable rather than asserted in a comment. It is deliberately *not* in
the report: peak concurrency legitimately varies between two identical runs, and a
report that varies cannot be diffed.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..verify.runner import DEFAULT_COMMAND_TIMEOUT, CommandRunner, run_command
from .adapters import RunAgent
from .report import RunReport, build_report
from .task import (
    DEFAULT_TIMEOUT_SECONDS,
    EvalTask,
    NeedsNetwork,
    Workspace,
    materialize,
)
from .taxonomy import (
    AgentOutcome,
    RunRecord,
    TaskResult,
    VerifyProbe,
    fixture_symbols,
    judge,
)

#: The agent's copy and the untouched copy, inside one task's temp directory.
WORK_DIRNAME: Final = "work"
BASELINE_DIRNAME: Final = "baseline"

#: How ``verify.sh`` is invoked. ``bash`` explicitly rather than relying on the
#: executable bit: a fixture copied out of a zip, a git checkout on a filesystem with
#: no exec bit, or a Windows-authored file all lose ``+x``, and "permission denied"
#: reported as a task failure is a harness bug masquerading as a model one.
VERIFY_ARGV_HEAD: Final[tuple[str, ...]] = ("bash",)

#: Why a result was skipped rather than failed.
SKIP_NEEDS_NETWORK: Final = "needs network"


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    """Everything the runner needs that is not a task or a model.

    ``model`` and ``label`` are strings carried into the report header. They are not
    used to *choose* a model — the adapter already decided that — so a mislabelled
    run is a wrong caption, never a wrong measurement.
    """

    parallel: int = 1
    #: Where per-task workspaces are created. ``None`` means a temp dir the run owns
    #: and deletes.
    workspace_root: Path | None = None
    #: Keep the workspaces after the run. The first thing anyone wants when a task
    #: fails is the tree it failed in.
    keep_workspaces: bool = False
    #: Attempt tasks whose fixture is a git revision. Off by default because the
    #: suite is required to run offline; on, they still fail rather than clone (see
    #: :class:`ronin.evals.task.NeedsNetwork`).
    allow_network: bool = False
    default_timeout: float = DEFAULT_TIMEOUT_SECONDS
    verify_timeout: float = DEFAULT_COMMAND_TIMEOUT
    model: str = ""
    label: str = ""
    suite_root: str = ""

    def __post_init__(self) -> None:
        if self.parallel < 1:
            raise ValueError("RunnerConfig.parallel must be >= 1")
        if self.default_timeout <= 0:
            raise ValueError("RunnerConfig.default_timeout must be positive")


class EvalRunner:
    """Runs a suite of tasks through one injected agent, bounded and isolated.

    Mutable because a run accumulates: :attr:`results` fills in as tasks finish and
    :attr:`peak_concurrency` climbs, and both have to survive a cancelled run — a
    ``KeyboardInterrupt`` halfway through sixty tasks should still leave the thirty
    that finished inspectable, which is impossible if the only output is a return
    value that never arrives.
    """

    def __init__(
        self,
        run_agent: RunAgent,
        *,
        config: RunnerConfig | None = None,
        command_runner: CommandRunner = run_command,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._run_agent = run_agent
        self._config = config if config is not None else RunnerConfig()
        self._command_runner = command_runner
        self._clock = clock
        self._results: list[TaskResult] = []
        self._live = 0
        self._peak = 0

    # ------------------------------------------------------------------ state

    @property
    def config(self) -> RunnerConfig:
        return self._config

    @property
    def results(self) -> tuple[TaskResult, ...]:
        """Every result so far, in completion order. Survives a cancelled run."""
        return tuple(self._results)

    @property
    def peak_concurrency(self) -> int:
        """The highest number of tasks in flight at once during this run."""
        return self._peak

    def report(self) -> RunReport:
        """A report over whatever has finished. Rows are ordered by ``(category, id)``."""
        return build_report(
            self._results,
            label=self._config.label,
            model=self._config.model,
            suite_root=self._config.suite_root,
        )

    # ------------------------------------------------------------------- run

    async def run(self, tasks: Sequence[EvalTask]) -> RunReport:
        """Run every task, at most ``parallel`` at a time, and build the report."""
        self._results = []
        self._live = 0
        self._peak = 0

        root = self._config.workspace_root
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
            await self._run_all(tasks, root)
        else:
            # `ignore_cleanup_errors` because a fixture may leave a read-only tree
            # behind and losing a temp directory must not lose the report.
            with tempfile.TemporaryDirectory(
                prefix="ronin-eval-", ignore_cleanup_errors=True
            ) as temp:
                await self._run_all(tasks, Path(temp))
        return self.report()

    async def _run_all(self, tasks: Sequence[EvalTask], root: Path) -> None:
        gate = asyncio.Semaphore(self._config.parallel)

        async def one(task: EvalTask) -> None:
            async with gate:
                self._live += 1
                self._peak = max(self._peak, self._live)
                try:
                    self._results.append(await self._run_task(task, root))
                finally:
                    self._live -= 1

        await asyncio.gather(*(one(task) for task in tasks))

    async def _run_task(self, task: EvalTask, root: Path) -> TaskResult:
        """One task, isolated, timed, and guaranteed to return a result.

        The timeout wraps everything including materialization, because a fixture with
        a pathological tree is as capable of hanging a run as a looping model.
        """
        started = self._clock()
        workspace = root / _safe_dirname(task.id)
        timeout = task.timeout_seconds or self._config.default_timeout
        try:
            return await asyncio.wait_for(
                self._attempt(task, workspace, started), timeout=timeout
            )
        except TimeoutError:
            return judge(
                self._record(
                    task,
                    workspace,
                    AgentOutcome(),
                    timed_out=True,
                    error=f"timed out after {timeout:g}s",
                    wall_seconds=self._clock() - started,
                )
            )
        except asyncio.CancelledError:
            # Well-formed on the way out, then re-raised: the caller who cancelled
            # still gets a report over `results`, and the cancellation is not
            # swallowed (a swallowed one turns Ctrl-C into a hang).
            self._results.append(
                judge(
                    self._record(
                        task,
                        workspace,
                        AgentOutcome(),
                        error="cancelled",
                        wall_seconds=self._clock() - started,
                    )
                )
            )
            raise
        except NeedsNetwork as exc:
            return judge(
                self._record(
                    task,
                    workspace,
                    AgentOutcome(),
                    skipped=True,
                    skip_reason=f"{SKIP_NEEDS_NETWORK}: {exc}",
                    wall_seconds=self._clock() - started,
                )
            )
        # Deliberately broad: an adapter is third-party-shaped and a fixture is
        # arbitrary. Anything either of them raises is this task's failure, not the
        # run's.
        except Exception as exc:
            return judge(
                self._record(
                    task,
                    workspace,
                    AgentOutcome(),
                    error=f"{type(exc).__name__}: {exc}",
                    wall_seconds=self._clock() - started,
                )
            )
        finally:
            if not self._config.keep_workspaces:
                shutil.rmtree(workspace, ignore_errors=True)

    async def _attempt(self, task: EvalTask, workspace: Path, started: float) -> TaskResult:
        if task.needs_network and not self._config.allow_network:
            return judge(
                self._record(
                    task,
                    workspace,
                    AgentOutcome(),
                    skipped=True,
                    skip_reason=(
                        f"{SKIP_NEEDS_NETWORK}: {task.git_url or '<no url>'}@"
                        f"{task.git_sha or '<no sha>'} — the suite runs offline"
                    ),
                    wall_seconds=self._clock() - started,
                )
            )

        work = materialize(task, workspace / WORK_DIRNAME)
        baseline = materialize(task, workspace / BASELINE_DIRNAME)
        before = await self._verify(baseline)
        symbols = fixture_symbols(baseline.root)

        outcome = await self._run_agent(task, work.root)

        after = await self._verify(work)
        return judge(
            self._record(
                task,
                workspace,
                outcome,
                verify_before=before,
                verify_after=after,
                known_symbols=symbols | fixture_symbols(work.root),
                wall_seconds=self._clock() - started,
            )
        )

    async def _verify(self, workspace: Workspace) -> VerifyProbe:
        """Run a workspace's ``verify.sh``, or say why there was nothing to run."""
        script = workspace.verify_script
        if script is None:
            return VerifyProbe(
                ran=False,
                detail=(
                    "no verify.sh in the fixture or beside task.toml, so this task has "
                    "no pass criterion and is counted as unverifiable"
                ),
            )
        outcome = await self._command_runner(
            [*VERIFY_ARGV_HEAD, script.name],
            cwd=workspace.root,
            timeout=self._config.verify_timeout,
        )
        return VerifyProbe(
            ran=True,
            exit_code=outcome.exit_code,
            output=outcome.output,
            duration_s=outcome.duration_s,
            detail=outcome.describe(),
        )

    def _record(
        self,
        task: EvalTask,
        workspace: Path,
        outcome: AgentOutcome,
        *,
        wall_seconds: float,
        verify_before: VerifyProbe | None = None,
        verify_after: VerifyProbe | None = None,
        known_symbols: frozenset[str] = frozenset(),
        timed_out: bool = False,
        skipped: bool = False,
        skip_reason: str = "",
        error: str = "",
    ) -> RunRecord:
        """The single place a :class:`RunRecord` is built, so no field is set twice."""
        return RunRecord(
            task_id=task.id,
            category=task.category,
            prompt=task.prompt,
            files_expected=task.files_expected,
            outcome=outcome,
            verify_before=verify_before if verify_before is not None else VerifyProbe(),
            verify_after=verify_after if verify_after is not None else VerifyProbe(),
            known_symbols=known_symbols,
            wall_seconds=wall_seconds,
            timed_out=timed_out,
            skipped=skipped,
            skip_reason=skip_reason,
            error=error,
            workspace=str(workspace),
        )


def _safe_dirname(task_id: str) -> str:
    """A task id as a directory name. Ids come from TOML and may hold anything."""
    cleaned = "".join(char if char.isalnum() or char in "-_." else "-" for char in task_id)
    return cleaned.strip("-.") or "task"


async def run_suite(
    tasks: Sequence[EvalTask],
    run_agent: RunAgent,
    *,
    config: RunnerConfig | None = None,
    command_runner: CommandRunner = run_command,
    clock: Callable[[], float] = time.monotonic,
) -> RunReport:
    """One-call form of :class:`EvalRunner`, for a caller with nothing to inspect.

    The class exists for the caller who does: a cancelled run's partial results live
    on the instance, and a function that only returns a report cannot expose them.
    """
    runner = EvalRunner(
        run_agent, config=config, command_runner=command_runner, clock=clock
    )
    return await runner.run(tasks)


@dataclass(frozen=True, slots=True)
class SuiteSelection:
    """A filter over a loaded suite. Empty fields mean "no constraint".

    Separated from the runner because ``ronin eval --category edit --tag flaky`` is
    argv parsing, and argv parsing that happens inside a runner is argv parsing that
    cannot be unit-tested without a runner.
    """

    ids: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()
    regression_gate_only: bool = False
    limit: int | None = None

    def apply(self, tasks: Sequence[EvalTask]) -> tuple[EvalTask, ...]:
        chosen = [
            task
            for task in tasks
            if (not self.ids or task.id in self.ids)
            and (not self.categories or task.category in self.categories)
            and (not self.tags or (self.tags & set(task.tags)))
            and (not self.regression_gate_only or task.regression_gate)
        ]
        return tuple(chosen[: self.limit] if self.limit is not None else chosen)


__all__ = [
    "BASELINE_DIRNAME",
    "SKIP_NEEDS_NETWORK",
    "VERIFY_ARGV_HEAD",
    "WORK_DIRNAME",
    "EvalRunner",
    "RunnerConfig",
    "SuiteSelection",
    "run_suite",
]
