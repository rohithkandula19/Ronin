"""SWE-bench-lite as a **second, external signal** — and never a tuning target.

The ronin eval suite (``ronin.evals.task`` / ``runner`` / ``taxonomy``) is written by
us, for us: its tasks encode what we *think* the agent should be good at, and the
failure taxonomy exists so a red run tells us whether to fix the harness or the
model. That makes it a superb development instrument and a worthless external claim,
because a suite you author and a suite you tune against are the same suite.

SWE-bench-lite is the counterweight. It is somebody else's tasks, scored by running
somebody else's tests, and its whole value is that **nothing here may be optimised
against it**. That is a rule the code enforces rather than a promise this docstring
makes, three ways:

1. **No transcript survives a run.** The harness's view of an agent
   (:class:`AgentLike` / :class:`AgentRunLike`) has no ``events`` member and no
   ``conversation`` member, and :class:`PatchAttempt` carries a unified diff plus
   scalar counters — nothing else. A supervised-fine-tuning or preference dataset is
   built from transcripts; there is no transcript here to build one from.
2. **Recording is forced off at the seam.** :func:`agent_patch_runner` opens every
   agent with ``record=False`` (:data:`RECORD_TRANSCRIPTS`) and exposes no way to
   turn it back on, so a run writes no ``.ronin/sessions/<id>.jsonl`` — which is the
   file a harvester would read.
3. **The writer refuses training sinks.** :func:`write_report` accepts ``.md`` and
   ``.json`` only and refuses any path under a training directory
   (:data:`TRAINING_SINK_NAMES`), because the training pipeline's row format is
   JSONL. A report also carries ``tuning_forbidden: true`` in
   :meth:`SWEBenchRun.to_json_dict`, so a pipeline that received a report anyway
   sees a machine-readable refusal rather than having to have read this docstring.

The other rule this module exists to keep is: **do not invent a resolve rate.** A
real SWE-bench-lite run needs the published dataset (network) and a per-repo test
environment (per-repo Python versions, compiled extensions, pinned dependencies).
Neither exists in CI, so the honest outcome is "did not run" — and that is enforced
by construction: a :class:`SWEBenchRun` with no outcomes and no ``did_not_run``
reason raises, and :attr:`SWEBenchRun.resolved_rate` is ``None`` rather than ``0.0``
when nothing ran. A ``0/0 (0.0%)`` headline is the exact failure this prevents.

The resolution rule itself is SWE-bench's, unchanged: a task is resolved iff every
``FAIL_TO_PASS`` test passes and every ``PASS_TO_PASS`` test still passes. See
:func:`is_resolved`.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# The disclaimer, and the things that make it true
# --------------------------------------------------------------------------- #

#: Rendered into every report. Stated in the output and not only in this module,
#: because the report is what a reviewer reads and a claim that lives only in the
#: source is a claim nobody sees.
SWEBENCH_DISCLAIMER: Final[str] = (
    "SWE-bench-lite is a second, external signal. It is not a tuning target: no "
    "prompt, adapter, dataset or gate in this repository may be selected, filtered or "
    "trained on the basis of these results. This harness retains no transcripts and "
    "records no session, so it cannot feed the training pipeline."
)

#: Passed to every agent this harness opens, and deliberately not a parameter. A
#: recorded session is a ``.ronin/sessions/<id>.jsonl`` transcript, and a transcript
#: is what a harvester turns into training rows.
RECORD_TRANSCRIPTS: Final[bool] = False

#: Path components :func:`write_report` refuses to write into. Not a security
#: boundary — a determined caller can always write a file itself — but it makes the
#: obvious mistake (``--out training/data/swebench.jsonl``) fail loudly at the one
#: place this module writes.
TRAINING_SINK_NAMES: Final[frozenset[str]] = frozenset(
    {"training", "sft", "dpo", "datasets", "eval_snapshots"}
)

#: The only suffixes a report may be written as. JSONL is excluded on purpose: it is
#: the training pipeline's row format, and a file that looks like a training shard is
#: a file something will eventually treat as one.
REPORT_SUFFIXES: Final[tuple[str, ...]] = (".md", ".json")

#: How much of a problem statement reaches the agent. Truncation is marked, because a
#: silently clipped issue makes an unresolved task look like a model failure when it
#: was a harness failure.
MAX_PROBLEM_CHARS: Final[int] = 12_000

_TRUNCATION_MARKER: Final[str] = "\n\n[…problem statement truncated: {cut} more characters]"


class SWEBenchError(RuntimeError):
    """Base class for the two things that go wrong before any task is scored."""


class DatasetUnavailable(SWEBenchError):
    """The dataset could not be loaded, with the reason a human can act on.

    Caught by :func:`run_or_report`, which turns it into a :class:`SWEBenchRun` whose
    ``did_not_run`` says so. That path is the reason this module never reports a rate
    it did not measure.
    """


class TrainingSinkRefused(SWEBenchError):
    """Raised by :func:`write_report` when asked to write into a training path."""


# --------------------------------------------------------------------------- #
# The dataset — the official schema, parsed without a dependency
# --------------------------------------------------------------------------- #


def _as_text(record: Mapping[str, object], name: str, default: str = "") -> str:
    value = record.get(name)
    return default if value is None else str(value)


def _as_tests(value: object) -> tuple[str, ...]:
    """Accept a list, or the JSON-encoded string the published dataset ships.

    SWE-bench's JSONL stores ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` as JSON *strings*
    (``'["test_a", "test_b"]'``), not arrays. A loader that only accepts arrays reads
    a real dataset row as "no required tests", and a task with no required tests
    cannot be resolved — so the whole run silently scores zero. Accept both.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        value = json.loads(stripped) if stripped else []
    if not isinstance(value, (list, tuple)):
        raise DatasetUnavailable(
            f"expected a list or a JSON-list string for a test list, got "
            f"{type(value).__name__} — the row is not in the SWE-bench schema"
        )
    return tuple(str(item) for item in value)


@dataclass(frozen=True, slots=True)
class SWEBenchTask:
    """One SWE-bench instance: a repo at a commit, an issue, and two test sets.

    Field names are the lower-snake house style; :meth:`from_record` accepts the
    dataset's UPPER_SNAKE keys, so a raw published row loads with no preprocessing
    step. ``patch`` is the *gold* patch and exists only for
    :func:`oracle_patch_runner` — it must never reach an agent's prompt, which is why
    :func:`default_prompt` does not read it.
    """

    instance_id: str
    repo: str = ""
    base_commit: str = ""
    problem_statement: str = ""
    test_patch: str = ""
    patch: str | None = None
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()
    hints_text: str = ""
    version: str = ""

    def __post_init__(self) -> None:
        if not self.instance_id:
            raise DatasetUnavailable("SWEBenchTask.instance_id is required and non-empty")

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> SWEBenchTask:
        """Build from one published row (or an equivalent hand-written fixture)."""
        gold = record.get("patch")
        return cls(
            instance_id=_as_text(record, "instance_id"),
            repo=_as_text(record, "repo"),
            base_commit=_as_text(record, "base_commit"),
            problem_statement=_as_text(record, "problem_statement"),
            test_patch=_as_text(record, "test_patch"),
            patch=None if gold is None else str(gold),
            fail_to_pass=_as_tests(record.get("FAIL_TO_PASS", record.get("fail_to_pass"))),
            pass_to_pass=_as_tests(record.get("PASS_TO_PASS", record.get("pass_to_pass"))),
            hints_text=_as_text(record, "hints_text"),
            version=_as_text(record, "version"),
        )

    @property
    def required_tests(self) -> tuple[str, ...]:
        """Everything that must pass for the task to count as resolved."""
        return (*self.fail_to_pass, *self.pass_to_pass)


@dataclass(frozen=True, slots=True)
class SWEBenchDataset:
    """A set of instances with unique ids, in the order they were loaded."""

    tasks: tuple[SWEBenchTask, ...] = ()

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for task in self.tasks:
            if task.instance_id in seen:
                raise DatasetUnavailable(f"duplicate instance_id: {task.instance_id}")
            seen.add(task.instance_id)

    def __iter__(self) -> Iterator[SWEBenchTask]:
        return iter(self.tasks)

    def __len__(self) -> int:
        return len(self.tasks)

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, object]]) -> SWEBenchDataset:
        return cls(tuple(SWEBenchTask.from_record(record) for record in records))

    def repos(self) -> tuple[str, ...]:
        return tuple(sorted({task.repo for task in self.tasks if task.repo}))

    def subset(
        self,
        *,
        ids: Sequence[str] | None = None,
        repo: str | None = None,
        limit: int | None = None,
    ) -> SWEBenchDataset:
        """Narrow by ``ids`` then ``repo`` then ``limit``, in that order."""
        tasks = list(self.tasks)
        if ids is not None:
            wanted = set(ids)
            tasks = [task for task in tasks if task.instance_id in wanted]
        if repo is not None:
            tasks = [task for task in tasks if task.repo == repo]
        if limit is not None:
            tasks = tasks[:limit]
        return SWEBenchDataset(tuple(tasks))


#: How rows arrive when they are not on disk. Injected rather than fetched, because
#: the published dataset lives on the network and this module must be usable — and
#: testable — with no network at all. A caller who wants the real thing supplies a
#: fetcher that knows how to get it; nothing here reaches for it.
DatasetFetcher = Callable[[], Iterable[Mapping[str, object]]]


def read_jsonl(path: Path) -> tuple[Mapping[str, object], ...]:
    """Rows from a JSONL file. Blank lines and ``#`` comments are skipped.

    Comments are permitted because the only SWE-bench rows this repository can
    honestly ship are hand-written fixtures, and a fixture needs to say so in-band.
    """
    if not path.is_file():
        raise DatasetUnavailable(f"no SWE-bench dataset at {path}")
    rows: list[Mapping[str, object]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetUnavailable(f"{path}:{number} is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise DatasetUnavailable(f"{path}:{number} is not a JSON object")
        rows.append(parsed)
    if not rows:
        raise DatasetUnavailable(f"{path} contains no dataset rows")
    return tuple(rows)


def load_dataset(
    *, path: Path | None = None, fetch: DatasetFetcher | None = None
) -> SWEBenchDataset:
    """Load from ``path``, else from ``fetch``, else refuse and say what is missing.

    Raises :class:`DatasetUnavailable` rather than returning an empty dataset: an
    empty dataset scored as ``0/0`` is the invented number this module exists to
    avoid, and the caller that wanted a run needs to know it did not get one.
    """
    if path is not None:
        return SWEBenchDataset.from_records(read_jsonl(path))
    if fetch is not None:
        return SWEBenchDataset.from_records(fetch())
    raise DatasetUnavailable(
        "no SWE-bench dataset: pass path= to a JSONL export, or fetch= to a callable "
        "that yields dataset rows. This module deliberately does not download it — the "
        "published dataset needs network access, and nothing here may reach for it."
    )


# --------------------------------------------------------------------------- #
# Scoring — SWE-bench's rule, unchanged
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TaskEvaluation:
    """Which named tests passed and failed once the patch was applied and run.

    A test named by the task but absent from both lists counts as *not passing* — a
    collection error, a skip or a crash is never a silent success.
    """

    passed_tests: tuple[str, ...] = ()
    failed_tests: tuple[str, ...] = ()
    error: str = ""


class Evaluator(Protocol):
    """Applies the test patch and the candidate patch, then runs the task's tests.

    Injected, and the reason there is no built-in one here: evaluating a real
    SWE-bench instance needs that repo's environment at that commit — its Python
    version, its compiled extensions, its pinned dependencies. That is a per-repo
    container, not a function, and pretending otherwise is how a harness reports
    failures that belong to the environment.
    """

    async def __call__(
        self, task: SWEBenchTask, patch: str, workdir: Path
    ) -> TaskEvaluation: ...


def is_resolved(task: SWEBenchTask, evaluation: TaskEvaluation) -> bool:
    """SWE-bench's resolution rule. Pure.

    Resolved iff every ``FAIL_TO_PASS`` test passed **and** every ``PASS_TO_PASS``
    test passed. Two edge cases carry weight:

    * a harness error (``evaluation.error``) is never resolved, however many tests
      passed — a build that half-ran proves nothing; and
    * a task with **no** required tests is never resolved, because "all zero of the
      required tests passed" is vacuously true and would score an unparseable row as
      a success.
    """
    if evaluation.error:
        return False
    passed = set(evaluation.passed_tests)
    required = task.required_tests
    return bool(required) and all(name in passed for name in required)


def _count_passed(passed: frozenset[str], required: Sequence[str]) -> int:
    return sum(1 for name in required if name in passed)


# --------------------------------------------------------------------------- #
# The agent seam — narrow on purpose
# --------------------------------------------------------------------------- #


@runtime_checkable
class AgentRunLike(Protocol):
    """The part of an agent result this harness is allowed to see.

    ``ronin.cli.sdk.AgentResult`` satisfies it structurally. Note what is *absent*:
    no ``events``, no ``state``, no ``notes``, no ``conversation``. That omission is
    the first of the three enforcement mechanisms in the module docstring — the
    harness cannot retain a transcript it has no way to name.
    """

    @property
    def text(self) -> str: ...

    @property
    def exit_code(self) -> int: ...


@runtime_checkable
class AgentLike(Protocol):
    """One opened agent: run a prompt, then release what it opened."""

    async def run(self, prompt: str) -> AgentRunLike: ...

    async def aclose(self) -> None: ...


class AgentOpener(Protocol):
    """Opens an agent on a directory. ``record`` is required, and always ``False``.

    ``ronin.cli.sdk.Agent.open`` satisfies this. It is a protocol rather than an
    import because ``ronin.evals`` may not depend on ``ronin.cli`` (see
    ``docs/ARCHITECTURE.md`` §3): the application layer wires the two together.
    """

    async def __call__(self, path: Path, *, record: bool) -> AgentLike: ...


#: Produces the unified diff of whatever the agent changed in a working directory.
#: Injected because the real implementation is ``git diff`` in a checkout, and a
#: checkout at a SWE-bench base commit is exactly what CI does not have.
DiffCapture = Callable[[Path], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class PatchAttempt:
    """What one agent produced for one task: a diff, and what it cost.

    Deliberately transcript-free. Every field is either the artefact being scored or
    a scalar counter; there is no field that could hold a message, an event or a tool
    call, so nothing this harness returns can be turned into a training row.
    """

    patch: str = ""
    turns: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    wall_seconds: float = 0.0
    note: str = ""

    @property
    def produced_patch(self) -> bool:
        return bool(self.patch.strip())


class PatchRunner(Protocol):
    """Attempts one task in ``workdir`` and returns the diff it produced."""

    async def __call__(self, task: SWEBenchTask, workdir: Path) -> PatchAttempt: ...


def default_prompt(task: SWEBenchTask) -> str:
    """The prompt one SWE-bench instance becomes.

    Says nothing about the required test names and nothing about the gold patch. Both
    would turn an execution-based benchmark into a fill-in-the-blank one, and a
    harness that leaks either measures the leak rather than the agent.
    """
    problem = task.problem_statement
    if len(problem) > MAX_PROBLEM_CHARS:
        cut = len(problem) - MAX_PROBLEM_CHARS
        problem = problem[:MAX_PROBLEM_CHARS] + _TRUNCATION_MARKER.format(cut=cut)
    where = f" at commit {task.base_commit}." if task.base_commit else "."
    lines = [
        f"You are working in a checkout of {task.repo or 'this repository'}{where}",
        "",
        "Resolve the following issue by editing the source. Do not edit or add tests —",
        "the tests that judge this change are supplied separately and will overwrite",
        "anything you write under a test path. Make the smallest change that fixes it.",
        "",
        "--- issue ---",
        problem,
    ]
    return "\n".join(lines) + "\n"


def agent_patch_runner(
    open_agent: AgentOpener,
    capture_diff: DiffCapture,
    *,
    prompt: Callable[[SWEBenchTask], str] = default_prompt,
    clock: Callable[[], float] = time.monotonic,
) -> PatchRunner:
    """The v2 adapter: drive ``ronin.cli.sdk.Agent`` at a SWE-bench instance.

    ``record=False`` is passed unconditionally and is not exposed as a parameter —
    mechanism 2 from the module docstring. A recorded run would leave a session
    transcript on disk, and a transcript on disk is a training row waiting to happen.

    Only the diff and the counters escape. The agent's answer text is read for
    nothing but a note, so that "the agent said it was done and changed no files" is
    distinguishable from "the agent crashed" — a distinction the resolve rate cannot
    make and the person debugging the harness needs.
    """

    async def run(task: SWEBenchTask, workdir: Path) -> PatchAttempt:
        started = clock()
        agent = await open_agent(workdir, record=RECORD_TRANSCRIPTS)
        try:
            result = await agent.run(prompt(task))
            exit_code = result.exit_code
        finally:
            await agent.aclose()
        patch = await capture_diff(workdir)
        elapsed = clock() - started
        note = ""
        if not patch.strip():
            note = f"agent exited {exit_code} and left the working tree unchanged"
        elif exit_code != 0:
            note = f"agent exited {exit_code} but did change files"
        return PatchAttempt(patch=patch, wall_seconds=elapsed, note=note)

    return run


def oracle_patch_runner() -> PatchRunner:
    """A runner that returns each task's **gold** patch. Not a score — a control.

    Gold patches should resolve their tasks. One that does not indicts the evaluator
    or the environment, never the agent, which makes this the first thing to run when
    a resolve rate looks wrong. Tasks with no gold patch yield no patch.
    """

    async def run(task: SWEBenchTask, workdir: Path) -> PatchAttempt:
        return PatchAttempt(patch=task.patch or "", note="gold patch (oracle control)")

    return run


def git_diff_capture(
    *, timeout: float = 120.0, args: Sequence[str] = ("diff", "--no-color")
) -> DiffCapture:
    """A :data:`DiffCapture` that asks ``git`` what changed in the working tree.

    The one real implementation, kept small and local: ``git`` is a local binary, so
    this is exercisable against a temporary repository with no network and no API
    key. A non-zero exit reports an empty diff and nothing else, because "git could
    not tell us" and "the agent changed nothing" score identically — both are "no
    patch" — and inventing a distinction would be inventing information.
    """

    async def capture(workdir: Path) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(workdir),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, _err = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return ""
        if process.returncode != 0:
            return ""
        return out.decode("utf-8", errors="replace")

    return capture


# --------------------------------------------------------------------------- #
# Outcomes and the run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SWEBenchOutcome:
    """One instance's result. Counters, not conversations."""

    instance_id: str
    resolved: bool = False
    patch_generated: bool = False
    fail_to_pass_passed: int = 0
    fail_to_pass_total: int = 0
    pass_to_pass_passed: int = 0
    pass_to_pass_total: int = 0
    turns: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    wall_seconds: float = 0.0
    error: str = ""
    note: str = ""

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if not self.patch_generated:
            return "no patch"
        return "resolved" if self.resolved else "unresolved"

    @property
    def partial(self) -> bool:
        """Some required tests passed, but not all of them.

        The interesting middle: at the headline rate, a patch that fixed the bug and
        broke something else looks identical to a patch that did nothing, and they
        are completely different failures.
        """
        if self.error or self.resolved:
            return False
        total = self.fail_to_pass_total + self.pass_to_pass_total
        passed = self.fail_to_pass_passed + self.pass_to_pass_passed
        return 0 < passed < total


@dataclass(frozen=True, slots=True)
class SWEBenchRun:
    """A whole run, or an honest statement that there wasn't one.

    ``did_not_run`` is the reason nothing was scored. The invariant enforced in
    :meth:`__post_init__` is the point of this class: a run with no outcomes **must**
    carry a reason, so the ``0/0 (0.0%)`` headline — a number that looks measured and
    is not — cannot be constructed at all. :attr:`resolved_rate` is ``None`` in that
    state rather than ``0.0``, which makes every caller handle the absence instead of
    formatting it.
    """

    outcomes: tuple[SWEBenchOutcome, ...] = ()
    did_not_run: str = ""
    dataset_size: int = 0
    model: str = ""
    label: str = ""

    def __post_init__(self) -> None:
        if not self.outcomes and not self.did_not_run:
            raise ValueError(
                "a SWEBenchRun with no outcomes must say why it did not run — an "
                "empty run reported as 0/0 is a resolve rate nobody measured"
            )
        if self.outcomes and self.did_not_run:
            raise ValueError(
                "a SWEBenchRun cannot both carry outcomes and claim it did not run"
            )

    @property
    def ran(self) -> bool:
        return not self.did_not_run

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def resolved(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.resolved)

    @property
    def patches_generated(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.patch_generated)

    @property
    def errored(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.error)

    @property
    def partial(self) -> tuple[str, ...]:
        return tuple(o.instance_id for o in self.outcomes if o.partial)

    @property
    def resolved_rate(self) -> float | None:
        """The headline, or ``None`` when there is no run to take a headline from."""
        if not self.ran or not self.outcomes:
            return None
        return round(self.resolved / len(self.outcomes), 4)

    def by_repo(self) -> dict[str, tuple[int, int]]:
        """``{repo_prefix: (resolved, total)}``, sorted by prefix.

        SWE-bench ids are ``owner__repo-<number>``; the key is everything before the
        final ``-``. Sorted by name rather than by rate so two runs of the same
        dataset render in the same order — a report you can diff.
        """
        buckets: dict[str, tuple[int, int]] = {}
        for outcome in self.outcomes:
            key = outcome.instance_id.rsplit("-", 1)[0] or outcome.instance_id
            resolved, total = buckets.get(key, (0, 0))
            buckets[key] = (resolved + int(outcome.resolved), total + 1)
        return dict(sorted(buckets.items()))

    def to_json_dict(self) -> dict[str, object]:
        """A serializable report, carrying the refusal machine-readably."""
        return {
            "tuning_forbidden": True,
            "transcripts_retained": False,
            "disclaimer": SWEBENCH_DISCLAIMER,
            "model": self.model,
            "label": self.label,
            "ran": self.ran,
            "did_not_run": self.did_not_run,
            "dataset_size": self.dataset_size,
            "total": self.total,
            "resolved": self.resolved if self.ran else None,
            "resolved_rate": self.resolved_rate,
            "patches_generated": self.patches_generated,
            "errored": self.errored,
            "partial": list(self.partial),
            "outcomes": [
                {f.name: getattr(outcome, f.name) for f in fields(outcome)}
                for outcome in self.outcomes
            ],
        }


def did_not_run(
    reason: str, *, dataset_size: int = 0, model: str = "", label: str = ""
) -> SWEBenchRun:
    """The honest empty run. ``reason`` must say what would produce a real one."""
    if not reason.strip():
        raise ValueError("did_not_run() needs a reason a human can act on")
    return SWEBenchRun(did_not_run=reason, dataset_size=dataset_size, model=model, label=label)


@dataclass(frozen=True, slots=True)
class SWEBenchHarness:
    """Runs a :class:`PatchRunner` over a dataset and scores it with an
    :class:`Evaluator`.

    One task's failure is that task's outcome, never the run's: a runner or evaluator
    that raises is recorded with its exception text and the run continues, because a
    benchmark that aborts on instance 3 of 300 tells you nothing about the other 297.

    Sequential on purpose. Concurrency here would multiply the per-repo environments
    in flight and make the wall-clock column meaningless, and the bottleneck a real
    run hits is the test environment, not the scheduler.
    """

    patch_runner: PatchRunner
    evaluator: Evaluator
    workspace: Callable[[SWEBenchTask], Path]
    model: str = ""
    label: str = ""

    async def run(self, dataset: SWEBenchDataset) -> SWEBenchRun:
        if len(dataset) == 0:
            return did_not_run(
                "the dataset was empty, so no instance was attempted",
                model=self.model,
                label=self.label,
            )
        outcomes = [await self._run_one(task) for task in dataset]
        return SWEBenchRun(
            outcomes=tuple(outcomes),
            dataset_size=len(dataset),
            model=self.model,
            label=self.label,
        )

    async def _run_one(self, task: SWEBenchTask) -> SWEBenchOutcome:
        base = SWEBenchOutcome(
            instance_id=task.instance_id,
            fail_to_pass_total=len(task.fail_to_pass),
            pass_to_pass_total=len(task.pass_to_pass),
        )
        workdir = self.workspace(task)
        try:
            attempt = await self.patch_runner(task, workdir)
        except Exception as exc:  # noqa: BLE001 — one instance may not kill the run
            return replace(base, error=f"patch runner failed: {exc!r}")

        counted = replace(
            base,
            turns=attempt.turns,
            tokens=attempt.tokens,
            cost_usd=attempt.cost_usd,
            wall_seconds=attempt.wall_seconds,
            note=attempt.note,
        )
        if not attempt.produced_patch:
            return counted

        try:
            evaluation = await self.evaluator(task, attempt.patch, workdir)
        except Exception as exc:  # noqa: BLE001
            return replace(counted, patch_generated=True, error=f"evaluator failed: {exc!r}")

        passed = frozenset(evaluation.passed_tests)
        return replace(
            counted,
            patch_generated=True,
            resolved=is_resolved(task, evaluation),
            fail_to_pass_passed=_count_passed(passed, task.fail_to_pass),
            pass_to_pass_passed=_count_passed(passed, task.pass_to_pass),
            error=evaluation.error,
        )


async def run_or_report(
    harness: SWEBenchHarness,
    *,
    path: Path | None = None,
    fetch: DatasetFetcher | None = None,
    ids: Sequence[str] | None = None,
    limit: int | None = None,
) -> SWEBenchRun:
    """Load the dataset and run it, or return a run that says why it could not.

    This is the entry point a CLI should call. It never lets
    :class:`DatasetUnavailable` reach the caller — a missing dataset is a *result*
    ("did not run, because …"), and a traceback there is how a CI job ends up
    republishing the previous run's number as if it were this run's.
    """
    try:
        dataset = load_dataset(path=path, fetch=fetch)
    except DatasetUnavailable as exc:
        return did_not_run(str(exc), model=harness.model, label=harness.label)
    if ids is not None or limit is not None:
        dataset = dataset.subset(ids=ids, limit=limit)
    return await harness.run(dataset)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _cell(value: str) -> str:
    """A value that cannot break out of its Markdown table cell."""
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def render_markdown(run: SWEBenchRun, *, include_repo_breakdown: bool = False) -> str:
    """A report that states the disclaimer and refuses to invent a headline.

    There is no flag to suppress :data:`SWEBENCH_DISCLAIMER`. A run that did not
    happen renders its reason and no table, because a table of nothing reads as a
    table of failures.
    """
    title = run.label or run.model or "SWE-bench-lite"
    if not run.ran:
        lines = [
            f"## {title} — did not run",
            "",
            f"> {SWEBENCH_DISCLAIMER}",
            "",
            f"**No resolve rate is reported, because no instance was scored.** "
            f"{run.did_not_run}",
            "",
            "A real run needs the published SWE-bench-lite dataset (network access) and a",
            "per-repo test environment at each instance's base commit. Supply the dataset",
            "with `path=` or `fetch=`, and an `Evaluator` that owns those environments.",
        ]
        return "\n".join(lines) + "\n"

    rate = run.resolved_rate
    if rate is None:
        raise RuntimeError(
            "SWEBenchRun.ran is true but it has no resolve rate — the invariant in "
            "SWEBenchRun.__post_init__ has been bypassed"
        )
    lines = [
        f"## {title} — resolved {run.resolved}/{run.total} ({rate:.1%})",
        "",
        f"> {SWEBENCH_DISCLAIMER}",
        "",
        f"patches generated {run.patches_generated}/{run.total} · "
        f"harness errors {run.errored} · partial {len(run.partial)}",
        "",
        "| Instance | Status | FAIL→PASS | PASS→PASS | Turns | Tokens | Cost | Wall | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for outcome in run.outcomes:
        note = outcome.error or outcome.note
        if outcome.partial:
            note = f"partial; {note}" if note else "partial"
        lines.append(
            f"| `{outcome.instance_id}` | {outcome.status} "
            f"| {outcome.fail_to_pass_passed}/{outcome.fail_to_pass_total} "
            f"| {outcome.pass_to_pass_passed}/{outcome.pass_to_pass_total} "
            f"| {outcome.turns} | {outcome.tokens} | ${outcome.cost_usd:.4f} "
            f"| {outcome.wall_seconds:.2f}s | {_cell(note)} |"
        )

    if include_repo_breakdown:
        breakdown = run.by_repo()
        if breakdown:
            lines += ["", "### By repo", "", "| Repo | Resolved |", "| --- | --- |"]
            for repo, (resolved, total) in breakdown.items():
                lines.append(f"| `{repo}` | {resolved}/{total} |")

    return "\n".join(lines) + "\n"


def write_report(run: SWEBenchRun, path: Path) -> None:
    """Write the report as Markdown (``.md``) or JSON (``.json``). Nothing else.

    Mechanism 3 from the module docstring. JSONL is refused because it is the
    training pipeline's row format, and any path under :data:`TRAINING_SINK_NAMES` is
    refused because that is where a dataset build would look. Neither check is a
    security boundary; both turn a plausible mistake into a loud one.
    """
    resolved = path.expanduser()
    parts = {part.lower() for part in resolved.parts}
    offending = sorted(parts & TRAINING_SINK_NAMES)
    if offending:
        raise TrainingSinkRefused(
            f"refusing to write a SWE-bench report to {path}: it is under a training "
            f"directory ({offending}), and SWE-bench-lite is a second external signal "
            "that nothing here may be tuned on. Write it under reports/ or docs/ instead."
        )
    if resolved.suffix not in REPORT_SUFFIXES:
        raise TrainingSinkRefused(
            f"refusing to write a SWE-bench report as {resolved.suffix or '(no suffix)'}: "
            f"allowed suffixes are {list(REPORT_SUFFIXES)}. JSONL in particular is the "
            "training pipeline's row format, and a report that looks like a training "
            "shard eventually gets treated as one."
        )
    body = (
        render_markdown(run, include_repo_breakdown=True)
        if resolved.suffix == ".md"
        else json.dumps(run.to_json_dict(), indent=2, sort_keys=True) + "\n"
    )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(body, encoding="utf-8")


__all__ = [
    "MAX_PROBLEM_CHARS",
    "RECORD_TRANSCRIPTS",
    "REPORT_SUFFIXES",
    "SWEBENCH_DISCLAIMER",
    "TRAINING_SINK_NAMES",
    "AgentLike",
    "AgentOpener",
    "AgentRunLike",
    "DatasetFetcher",
    "DatasetUnavailable",
    "DiffCapture",
    "Evaluator",
    "PatchAttempt",
    "PatchRunner",
    "SWEBenchDataset",
    "SWEBenchError",
    "SWEBenchHarness",
    "SWEBenchOutcome",
    "SWEBenchRun",
    "SWEBenchTask",
    "TaskEvaluation",
    "TrainingSinkRefused",
    "agent_patch_runner",
    "default_prompt",
    "did_not_run",
    "git_diff_capture",
    "is_resolved",
    "load_dataset",
    "oracle_patch_runner",
    "read_jsonl",
    "render_markdown",
    "run_or_report",
    "write_report",
]
