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


def make_local_git_evaluator(
    repo_root: str | Path,
    *,
    test_command: list[str] | None = None,
    timeout: int = 1800,
) -> Evaluator:
    """Reference evaluator over a **local git checkout** — no Docker.

    For each task it: hard-resets the working tree to ``base_commit``, applies
    the task's ``test_patch`` then the candidate patch with ``git apply``, runs
    the task's tests with ``test_command`` (default: ``python -m pytest``), and
    parses pytest node-id PASSED/FAILED results.

    ⚠️ This mutates ``repo_root`` (it runs ``git reset --hard`` + ``git clean``).
    Point it at a disposable clone, never a working tree with changes you want.
    """
    root = Path(repo_root)
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
