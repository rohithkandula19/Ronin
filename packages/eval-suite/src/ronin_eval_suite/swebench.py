"""SWE-bench harness — execution-based agentic coding eval.

Unlike the LLM-as-a-judge :class:`~ronin_eval_suite.suite.EvalSuite`, SWE-bench
scores by *running tests*, not by asking a model. Each task is a real GitHub
issue: a repo at a base commit, a problem statement, and two sets of tests:

- ``FAIL_TO_PASS`` — tests that fail before the fix and must pass after it.
- ``PASS_TO_PASS`` — tests that already pass and must keep passing (no regressions).

A task is *resolved* iff, after applying the agent's patch, **every**
``FAIL_TO_PASS`` test passes and **every** ``PASS_TO_PASS`` test still passes.
That single rule is the whole benchmark; everything here orchestrates it.

The harness is execution-agnostic: you plug in

- a ``patch_runner`` — your agent; takes a :class:`SWEBenchTask`, returns a
  unified-diff string (empty string = "I produced no patch"); and
- an ``evaluator`` — applies the test patch + candidate patch and reports which
  tests passed. :func:`make_local_git_evaluator` is a ready subprocess-based
  reference for a local checkout; tests inject a mock.

No API key, Docker, or network is required to use the harness itself — those are
concerns of whichever ``evaluator`` and ``patch_runner`` you supply.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Iterator, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _coerce_test_list(value: object) -> list[str]:
    """Accept a list, or the JSON-encoded string the official dataset ships.

    SWE-bench's published JSONL stores ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` as
    JSON *strings* (e.g. ``"[\\"test_a\\", \\"test_b\\"]"``), not arrays. Accept
    both so a raw HuggingFace export loads without a preprocessing step.
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value) if value.strip() else []
    if not isinstance(value, list):
        raise TypeError(f"expected list or JSON-list string, got {type(value).__name__}")
    return [str(v) for v in value]


class SWEBenchTask(BaseModel):
    """One SWE-bench instance.

    Field aliases mirror the official dataset's UPPER_SNAKE names so a raw line
    validates directly; populate by either name.
    """

    model_config = ConfigDict(populate_by_name=True)

    instance_id: str
    repo: str = ""
    base_commit: str = ""
    problem_statement: str = ""
    test_patch: str = ""
    patch: str | None = None  # the gold patch — for oracle runs / reference only
    fail_to_pass: list[str] = Field(default_factory=list, alias="FAIL_TO_PASS")
    pass_to_pass: list[str] = Field(default_factory=list, alias="PASS_TO_PASS")
    hints_text: str = Field(default="", alias="hints_text")
    version: str = ""

    _coerce_ftp = field_validator("fail_to_pass", mode="before")(_coerce_test_list)
    _coerce_ptp = field_validator("pass_to_pass", mode="before")(_coerce_test_list)


class SWEBenchDataset:
    """JSONL-backed collection of :class:`SWEBenchTask` (mirrors ``GoldenDataset``).

    Each line is one JSON object. ``instance_id`` is required and unique. Blank
    lines and lines starting with ``#`` are skipped.
    """

    def __init__(self, tasks: list[SWEBenchTask]):
        seen: set[str] = set()
        for t in tasks:
            if t.instance_id in seen:
                raise ValueError(f"duplicate instance_id: {t.instance_id}")
            seen.add(t.instance_id)
        self.tasks = tasks

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "SWEBenchDataset":
        tasks: list[SWEBenchTask] = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tasks.append(SWEBenchTask.model_validate_json(line))
        return cls(tasks)

    def __iter__(self) -> Iterator[SWEBenchTask]:
        return iter(self.tasks)

    def __len__(self) -> int:
        return len(self.tasks)

    def repos(self) -> list[str]:
        """Sorted unique repos in the dataset."""
        return sorted({t.repo for t in self.tasks if t.repo})

    def subset(
        self,
        *,
        ids: list[str] | None = None,
        repo: str | None = None,
        limit: int | None = None,
    ) -> "SWEBenchDataset":
        """Return a new dataset narrowed by ``ids`` / ``repo`` / ``limit`` (applied in that order).

        Handy for smoke runs (``limit=5``) or single-repo evaluation, since the
        local-git evaluator targets one checkout at a time.
        """
        tasks = list(self.tasks)
        if ids is not None:
            wanted = set(ids)
            tasks = [t for t in tasks if t.instance_id in wanted]
        if repo is not None:
            tasks = [t for t in tasks if t.repo == repo]
        if limit is not None:
            tasks = tasks[:limit]
        return SWEBenchDataset(tasks)


class TaskEvaluation(BaseModel):
    """Which named tests passed / failed after a patch was applied and run.

    A test absent from both lists counts as *not passing* for resolution — a
    missing required test is never a silent success.
    """

    passed_tests: list[str] = Field(default_factory=list)
    failed_tests: list[str] = Field(default_factory=list)
    error: str | None = None  # set when the patch/build/test harness itself blew up


class Evaluator(Protocol):
    """Applies the test patch + candidate patch and runs the task's tests."""

    def __call__(self, task: SWEBenchTask, candidate_patch: str) -> TaskEvaluation: ...


def is_resolved(task: SWEBenchTask, evaluation: TaskEvaluation) -> bool:
    """The canonical SWE-bench resolution rule.

    Resolved iff every ``FAIL_TO_PASS`` test passed and every ``PASS_TO_PASS``
    test passed. A failed harness (``evaluation.error``) is never resolved.
    """
    if evaluation.error is not None:
        return False
    passed = set(evaluation.passed_tests)
    required = list(task.fail_to_pass) + list(task.pass_to_pass)
    return bool(required) and all(t in passed for t in required)


class SWEBenchResult(BaseModel):
    """Per-task outcome, serializable to JSON."""

    instance_id: str
    resolved: bool
    patch_generated: bool
    fail_to_pass_passed: int = 0
    fail_to_pass_total: int = 0
    pass_to_pass_passed: int = 0
    pass_to_pass_total: int = 0
    error: str | None = None


class SWEBenchReport(BaseModel):
    """Result of running an agent over a dataset; ``summary`` holds the headline rate."""

    model: str = ""
    label: str | None = None
    results: list[SWEBenchResult] = Field(default_factory=list)
    summary: dict[str, float] = Field(default_factory=dict)

    def compute_summary(self) -> None:
        total = len(self.results)
        resolved = sum(1 for r in self.results if r.resolved)
        generated = sum(1 for r in self.results if r.patch_generated)
        errored = sum(1 for r in self.results if r.error is not None)
        self.summary = {
            "total": float(total),
            "resolved": float(resolved),
            "resolved_rate": round(resolved / total, 4) if total else 0.0,
            "patch_generated": float(generated),
            "errored": float(errored),
        }

    def by_repo(self) -> dict[str, dict[str, float]]:
        """Per-repo ``{resolved, total, resolved_rate}``, keyed by the repo prefix.

        SWE-bench instance ids are ``owner__repo-<number>``; the key is the part
        before the final ``-`` (e.g. ``django__django``). Repos are returned in
        descending resolved-rate order so the weakest repos surface last.
        """
        buckets: dict[str, list[SWEBenchResult]] = {}
        for r in self.results:
            key = r.instance_id.rsplit("-", 1)[0] or r.instance_id
            buckets.setdefault(key, []).append(r)

        out: dict[str, dict[str, float]] = {}
        for key, rs in buckets.items():
            total = len(rs)
            resolved = sum(1 for r in rs if r.resolved)
            out[key] = {
                "resolved": float(resolved),
                "total": float(total),
                "resolved_rate": round(resolved / total, 4) if total else 0.0,
            }
        return dict(
            sorted(out.items(), key=lambda kv: (-kv[1]["resolved_rate"], kv[0]))
        )


def _count(passed: set[str], required: list[str]) -> int:
    return sum(1 for t in required if t in passed)


class SWEBenchHarness(BaseModel):
    """Runs an agent (``patch_runner``) over tasks and scores each via ``evaluator``.

        harness = SWEBenchHarness(patch_runner=my_agent, evaluator=make_local_git_evaluator(repo))
        report = harness.run(dataset)
        print(report.summary["resolved_rate"])

    A per-task failure in either the runner or the evaluator is recorded on that
    task's :class:`SWEBenchResult` and the run continues.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    patch_runner: Callable[[SWEBenchTask], str]
    evaluator: Callable[[SWEBenchTask, str], TaskEvaluation]
    model: str = ""
    label: str | None = None

    def run(self, dataset: SWEBenchDataset) -> SWEBenchReport:
        results: list[SWEBenchResult] = []
        for task in dataset:
            results.append(self._run_one(task))
        report = SWEBenchReport(model=self.model, label=self.label, results=results)
        report.compute_summary()
        return report

    def _run_one(self, task: SWEBenchTask) -> SWEBenchResult:
        try:
            patch = self.patch_runner(task)
        except Exception as exc:  # noqa: BLE001 — one task's failure must not kill the run
            return SWEBenchResult(
                instance_id=task.instance_id,
                resolved=False,
                patch_generated=False,
                fail_to_pass_total=len(task.fail_to_pass),
                pass_to_pass_total=len(task.pass_to_pass),
                error=f"patch_runner failed: {exc}",
            )

        if not patch or not patch.strip():
            return SWEBenchResult(
                instance_id=task.instance_id,
                resolved=False,
                patch_generated=False,
                fail_to_pass_total=len(task.fail_to_pass),
                pass_to_pass_total=len(task.pass_to_pass),
            )

        try:
            evaluation = self.evaluator(task, patch)
        except Exception as exc:  # noqa: BLE001
            return SWEBenchResult(
                instance_id=task.instance_id,
                resolved=False,
                patch_generated=True,
                fail_to_pass_total=len(task.fail_to_pass),
                pass_to_pass_total=len(task.pass_to_pass),
                error=f"evaluator failed: {exc}",
            )

        passed = set(evaluation.passed_tests)
        return SWEBenchResult(
            instance_id=task.instance_id,
            resolved=is_resolved(task, evaluation),
            patch_generated=True,
            fail_to_pass_passed=_count(passed, task.fail_to_pass),
            fail_to_pass_total=len(task.fail_to_pass),
            pass_to_pass_passed=_count(passed, task.pass_to_pass),
            pass_to_pass_total=len(task.pass_to_pass),
            error=evaluation.error,
        )


class SWEBenchComparison(BaseModel):
    """Diff of two runs: which instances changed resolution status."""

    newly_resolved: list[str] = Field(default_factory=list)  # unresolved -> resolved
    newly_broken: list[str] = Field(default_factory=list)  # resolved -> unresolved (regression)
    still_resolved: list[str] = Field(default_factory=list)
    still_unresolved: list[str] = Field(default_factory=list)
    baseline_rate: float = 0.0
    candidate_rate: float = 0.0

    @property
    def has_regression(self) -> bool:
        return bool(self.newly_broken)

    @property
    def rate_delta(self) -> float:
        return round(self.candidate_rate - self.baseline_rate, 4)


def compare_swebench(baseline: SWEBenchReport, candidate: SWEBenchReport) -> SWEBenchComparison:
    """Compare two reports by per-instance resolution.

    Only instances present in **both** runs are classified, so a partial
    candidate run can't masquerade as a regression. ``has_regression`` is true
    iff any instance went resolved -> unresolved — wire it into CI to block
    quality drops.
    """
    base = {r.instance_id: r.resolved for r in baseline.results}
    cand = {r.instance_id: r.resolved for r in candidate.results}
    common = base.keys() & cand.keys()

    cmp = SWEBenchComparison(
        baseline_rate=baseline.summary.get("resolved_rate", 0.0),
        candidate_rate=candidate.summary.get("resolved_rate", 0.0),
    )
    for iid in sorted(common):
        b, c = base[iid], cand[iid]
        if c and not b:
            cmp.newly_resolved.append(iid)
        elif b and not c:
            cmp.newly_broken.append(iid)
        elif b and c:
            cmp.still_resolved.append(iid)
        else:
            cmp.still_unresolved.append(iid)
    return cmp


def generate_predictions(
    dataset: SWEBenchDataset,
    patch_runner: Callable[[SWEBenchTask], str],
    *,
    model: str = "",
) -> list[dict[str, str]]:
    """Run ``patch_runner`` over a dataset and collect SWE-bench prediction rows.

    Returns one ``{instance_id, model_patch, model_name_or_path}`` dict per task
    — the standard prediction schema. This is the *generate* half of SWE-bench's
    two-phase flow; pair it with :func:`write_predictions` to persist, then score
    the file later (decoupling slow patch generation from evaluation). A runner
    that raises on a task yields an empty patch for it, never aborting the batch.
    """
    rows: list[dict[str, str]] = []
    for task in dataset:
        try:
            patch = patch_runner(task)
        except Exception:  # noqa: BLE001 — one task must not abort generation
            patch = ""
        rows.append(
            {
                "instance_id": task.instance_id,
                "model_patch": patch or "",
                "model_name_or_path": model,
            }
        )
    return rows


def write_predictions(
    path: str | Path, predictions: list[dict[str, str]]
) -> None:
    """Write prediction rows as JSONL (the format ``csk-eval swebench`` reads)."""
    Path(path).write_text(
        "\n".join(json.dumps(row) for row in predictions) + "\n",
        encoding="utf-8",
    )


def oracle_runner(task: SWEBenchTask) -> str:
    """A ``patch_runner`` that returns each task's **gold** patch.

    Feed this to :class:`SWEBenchHarness` to sanity-check your evaluator and
    environment: gold patches *should* resolve their tasks. A gold patch that
    fails to resolve points at the harness/environment, not the agent. Tasks
    with no gold ``patch`` yield an empty string (counted as no-patch).
    """
    return task.patch or ""


def render_swebench_markdown(
    report: SWEBenchReport, *, include_repo_breakdown: bool = False
) -> str:
    """Render a report as a Markdown summary + per-instance table.

    Designed to paste directly into a PR comment or README. The headline line
    is the resolved rate; the table lists each instance with its FAIL_TO_PASS /
    PASS_TO_PASS tallies and a status glyph. With ``include_repo_breakdown`` a
    per-repo resolved-rate table (via :meth:`SWEBenchReport.by_repo`) is appended.
    """
    s = report.summary or {}
    total = int(s.get("total", len(report.results)))
    resolved = int(s.get("resolved", sum(1 for r in report.results if r.resolved)))
    rate = s.get("resolved_rate", (resolved / total if total else 0.0))

    title = report.label or report.model or "SWE-bench"
    lines = [
        f"## {title} — resolved {resolved}/{total} ({rate:.1%})",
        "",
        "| Instance | Status | FAIL→PASS | PASS→PASS | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in report.results:
        if r.error:
            status, note = "⚠️ error", r.error
        elif not r.patch_generated:
            status, note = "∅ no patch", "agent produced no patch"
        elif r.resolved:
            status, note = "✅ resolved", ""
        else:
            status, note = "❌ unresolved", ""
        lines.append(
            f"| `{r.instance_id}` | {status} "
            f"| {r.fail_to_pass_passed}/{r.fail_to_pass_total} "
            f"| {r.pass_to_pass_passed}/{r.pass_to_pass_total} "
            f"| {note} |"
        )

    if include_repo_breakdown:
        breakdown = report.by_repo()
        if breakdown:
            lines += ["", "### By repo", "", "| Repo | Resolved | Rate |", "| --- | --- | --- |"]
            for repo, stats in breakdown.items():
                lines.append(
                    f"| `{repo}` | {int(stats['resolved'])}/{int(stats['total'])} "
                    f"| {stats['resolved_rate']:.1%} |"
                )

    return "\n".join(lines) + "\n"


class DirtyWorkingTreeError(RuntimeError):
    """Raised when the benchmark would destroy uncommitted work."""


# Top-level names that ``git clean -fdx`` will remove but which are regenerable —
# a benchmark clone is SUPPOSED to wipe these, and refusing on them would make the
# guard fire on almost every real repo. Anything OUTSIDE this set that clean would
# remove (a ``.env``, a data file, notes) is treated as "you would lose it".
_REGENERABLE = frozenset({
    ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".nox", "dist", "build", ".eggs",
    ".next", "target", "htmlcov", ".coverage", ".cache", ".gradle", "vendor",
})


def _ignored_at_risk(root: Path) -> list[str]:
    """Git-IGNORED files that ``git clean -fdx`` would delete and that are not
    obviously regenerable — the gap ``git status --porcelain`` misses entirely
    (it does not list ignored files, so a real ``.env`` reads as a clean tree).
    """
    proc = subprocess.run(
        ["git", "-C", str(root), "clean", "-fdxn"],   # -n = dry run, deletes nothing
        capture_output=True, text=True,
    )
    at_risk: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.startswith("Would remove "):
            continue
        path = line[len("Would remove "):].strip()
        top = path.strip("/").split("/", 1)[0]
        if top not in _REGENERABLE and not top.endswith(".egg-info"):
            at_risk.append(path)
    return at_risk


def _assert_disposable(root: Path, allow_dirty: bool) -> None:
    """Refuse to hard-reset a working tree that has anything to lose.

    ``evaluate()`` runs ``git reset --hard`` + ``git clean -fdx``, which discards
    uncommitted changes, untracked files, AND git-ignored files. The docstring used
    to just *warn* ("point it at a disposable clone") — but a warning is not a
    guard, and nothing stopped you pointing this at the repo you actually work in
    and losing it.

    On a CLEAN tree those commands destroy nothing, so gate exactly on that: if the
    tree is clean, proceed; if it has anything to lose, refuse and say what would
    die. Two sources of loss, because ``git status --porcelain`` catches only one:

    - tracked/untracked changes (porcelain), and
    - **git-ignored** files ``clean -fdx`` still deletes — a real ``.env`` reads as
      a clean tree to porcelain but is very much something you would lose. Only
      genuinely regenerable ignored paths (``.venv`` / ``node_modules`` / caches,
      see :data:`_REGENERABLE`) are allowed through.

    ``allow_dirty=True`` is the explicit, deliberate override.

    Checked once at construction, not per task — after the first task the tree is
    *legitimately* dirty (the evaluator applied the patches itself), so a per-task
    check would refuse to run task 2.
    """
    if allow_dirty:
        return

    proc = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise DirtyWorkingTreeError(
            f"{root} is not a git repository (or git failed) — refusing to run "
            "`git reset --hard` / `git clean -fdx` there."
        )

    dirty = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if dirty:
        shown = "\n".join(f"    {ln}" for ln in dirty[:10])
        more = f"\n    … and {len(dirty) - 10} more" if len(dirty) > 10 else ""
        raise DirtyWorkingTreeError(
            f"refusing to benchmark against {root}: it has {len(dirty)} uncommitted "
            "or untracked change(s), and this evaluator runs `git reset --hard` + "
            "`git clean -fdx` before every task — which would destroy them:\n"
            f"{shown}{more}\n"
            "Point it at a disposable clone, or pass allow_dirty=True if you really "
            "mean to throw this work away."
        )

    at_risk = _ignored_at_risk(root)
    if at_risk:
        shown = "\n".join(f"    {p}" for p in at_risk[:10])
        more = f"\n    … and {len(at_risk) - 10} more" if len(at_risk) > 10 else ""
        raise DirtyWorkingTreeError(
            f"refusing to benchmark against {root}: `git clean -fdx` would delete "
            f"{len(at_risk)} git-ignored file(s) that are not regenerable — a "
            "porcelain-clean tree can still hide a real .env or data file here:\n"
            f"{shown}{more}\n"
            "Point it at a disposable clone, or pass allow_dirty=True to override."
        )


def make_local_git_evaluator(
    repo_root: str | Path,
    *,
    test_command: list[str] | None = None,
    timeout: int = 1800,
    allow_dirty: bool = False,
) -> Evaluator:
    """Reference evaluator over a **local git checkout** — no Docker.

    For each task it: hard-resets the working tree to ``base_commit``, applies
    the task's ``test_patch`` then the candidate patch with ``git apply``, runs
    the task's tests with ``test_command`` (default: ``python -m pytest``), and
    parses pytest node-id PASSED/FAILED results.

    ⚠️ This mutates ``repo_root``: it runs ``git reset --hard`` + ``git clean -fdx``
    before every task. That is fine on a disposable clone and catastrophic on a tree
    you were working in, so it is now **enforced, not merely documented**: if
    ``repo_root`` has uncommitted or untracked changes this raises
    :class:`DirtyWorkingTreeError` instead of destroying them. Pass
    ``allow_dirty=True`` to override deliberately.

    Note ``git clean -fdx`` also removes git-ignored files (``.env``, ``.venv``).
    That is the benchmark's intended clean slate, but it is another reason to run
    this against a throwaway checkout.
    """
    root = Path(repo_root)
    _assert_disposable(root, allow_dirty)   # before we take ownership of the tree
    base_cmd = test_command or ["python", "-m", "pytest"]

    def _git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _apply(patch: str) -> None:
        proc = subprocess.run(
            ["git", "-C", str(root), "apply", "-"],
            input=patch,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git apply failed: {proc.stderr.strip()}")

    def evaluate(task: SWEBenchTask, candidate_patch: str) -> TaskEvaluation:
        if task.base_commit:
            _git("reset", "--hard", task.base_commit)
            _git("clean", "-fdx")
        if task.test_patch.strip():
            _apply(task.test_patch)
        _apply(candidate_patch)

        node_ids = list(task.fail_to_pass) + list(task.pass_to_pass)
        proc = subprocess.run(
            [*base_cmd, "-p", "no:cacheprovider", "-rA", "-q", *node_ids],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return _parse_pytest_results(proc.stdout, node_ids)

    return evaluate


def _parse_pytest_results(stdout: str, node_ids: list[str]) -> TaskEvaluation:
    """Parse pytest ``-rA`` short-summary lines (``PASSED nodeid`` / ``FAILED nodeid``).

    Tests neither reported PASSED nor FAILED (collection error, skipped, crash)
    are left out of ``passed_tests`` so they cannot satisfy resolution.
    """
    passed: list[str] = []
    failed: list[str] = []
    wanted = set(node_ids)
    for raw in stdout.splitlines():
        line = raw.strip()
        for status, bucket in (("PASSED ", passed), ("FAILED ", failed), ("ERROR ", failed)):
            if line.startswith(status):
                nid = line[len(status):].strip()
                if nid in wanted:
                    bucket.append(nid)
    return TaskEvaluation(passed_tests=passed, failed_tests=failed)
