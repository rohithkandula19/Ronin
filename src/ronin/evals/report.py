"""Reports: per-task rows, aggregates, the taxonomy breakdown, and a scoreboard.

Two emissions. **JSON is the machine record** — stable key order, no wall-clock paths,
nothing that varies between two identical runs, so `diff` is a usable tool on it.
**Markdown is for a human**, and it prints ``—`` wherever a number does not exist
rather than a zero. That is not politeness: a pass rate of ``0%`` over zero tasks and
a token count of ``0`` from a provider that reported nothing are both lies that look
like measurements, and this whole phase exists to stop producing those.

Three properties are enforced rather than intended.

**Unclassified failures are reported next to the taxonomy, not under it.** A taxonomy
that absorbs everything into one bucket reads as insight and is noise; the
unclassified count is the error bar on the rest of the table, so it is printed at the
same prominence as the classes.

**Unreported usage is never averaged in.** Tokens and cost distributions are computed
over the tasks whose provider actually reported, and the count that did not is
carried beside the number. Folding a silent provider in as a zero is how a session
report ends up claiming a run was free.

**Captured output is clamped deterministically and marked.** ``verify.sh`` output goes
through :func:`ronin.context.budget.clamp`, which keeps head and tail and states the
exact number of lines elided. A row that silently dropped the end of a pytest run
would hide the failure the row exists to explain.

``packages/eval-suite``'s ``render_html_report`` was read and is **not** reused: it
renders a judge's rubric scores (``RunReport.rubric.criteria``, a per-criterion
histogram over an integer scale) from pydantic models, and this suite has no rubric
and no judge — its unit of truth is a green or red ``verify.sh``. Bending one into the
other would mean inventing criteria, which is the one thing forbidden here.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from ..context.budget import OutputBudget, clamp
from .taxonomy import BLAME, FailureClass, Finding, TaskResult, count_classes

#: How much captured ``verify.sh`` output a report row carries. Enough for a pytest
#: summary at both ends; the full output lives in the workspace when
#: ``keep_workspaces`` is on.
ROW_OUTPUT_CHARS: Final = 2_000

#: What the markdown renderer prints where a value does not exist. Never ``0``.
ABSENT: Final = "—"

#: Which named metrics get a distribution. Order is the report's column order.
DISTRIBUTION_NAMES: Final[tuple[str, ...]] = ("turns", "tokens", "cost_usd", "wall_seconds")


def _round(value: float) -> float:
    """Six decimals, so a float never differs in its last bit between two runs."""
    return round(value, 6)


# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Aggregate:
    """Counts over a set of tasks. ``pass_rate`` is ``None`` when there is nothing to rate."""

    tasks: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    unverifiable: int = 0
    errored: int = 0
    timed_out: int = 0

    @property
    def attempted(self) -> int:
        """Tasks that actually ran. Skips are excluded from every rate."""
        return self.tasks - self.skipped

    @property
    def pass_rate(self) -> float | None:
        """Passes over attempted tasks, or ``None`` — never ``0.0`` for an empty set."""
        return self.passed / self.attempted if self.attempted else None

    def render_pass_rate(self) -> str:
        rate = self.pass_rate
        return ABSENT if rate is None else f"{rate:.1%}"


def aggregate(results: Sequence[TaskResult]) -> Aggregate:
    """Fold results into counts. Every task lands in exactly one of pass/fail/skip."""
    return Aggregate(
        tasks=len(results),
        passed=sum(1 for result in results if result.passed),
        failed=sum(1 for result in results if result.record.failed),
        skipped=sum(1 for result in results if result.skipped),
        unverifiable=sum(1 for result in results if result.record.unverifiable),
        errored=sum(1 for result in results if result.record.failed_run),
        timed_out=sum(1 for result in results if result.record.timed_out),
    )


@dataclass(frozen=True, slots=True)
class Distribution:
    """One metric's spread over the tasks that reported it.

    ``count`` is the number of tasks that contributed, and ``absent`` the number that
    did not — a provider that reported no usage, or a task that never ran. The two are
    kept apart so ``mean`` is a mean of real values and nothing else.
    """

    name: str
    count: int = 0
    absent: int = 0
    total: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0
    mean: float = 0.0
    median: float = 0.0
    p90: float = 0.0

    @property
    def measured(self) -> bool:
        return self.count > 0

    def render(self, *, precision: int = 1) -> str:
        if not self.measured:
            return ABSENT
        return (
            f"min {self.minimum:.{precision}f} · med {self.median:.{precision}f} · "
            f"p90 {self.p90:.{precision}f} · max {self.maximum:.{precision}f}"
        )


def distribution(name: str, values: Sequence[float], *, absent: int = 0) -> Distribution:
    """Spread of ``values``. An empty input is an empty distribution, not zeros."""
    if not values:
        return Distribution(name=name, count=0, absent=absent)
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    median = ordered[middle] if count % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    # ceil-of-rank rather than interpolation: an integer index is identical on every
    # platform, and an interpolated p90 over eight tasks is false precision anyway.
    p90_index = min(count - 1, max(0, math.ceil(0.9 * count) - 1))
    return Distribution(
        name=name,
        count=count,
        absent=absent,
        total=_round(sum(ordered)),
        minimum=_round(ordered[0]),
        maximum=_round(ordered[-1]),
        mean=_round(sum(ordered) / count),
        median=_round(median),
        p90=_round(ordered[p90_index]),
    )


@dataclass(frozen=True, slots=True)
class TaxonomyBreakdown:
    """How the failures split, with the unclassified count kept in the open."""

    counts: Mapping[FailureClass, int] = field(default_factory=dict)
    failures: int = 0
    unclassified: int = 0
    #: Failures counted against each side of the system, from ``taxonomy.BLAME``.
    blame: Mapping[str, int] = field(default_factory=dict)

    @property
    def classified(self) -> int:
        return self.failures - self.unclassified

    def render_unclassified(self) -> str:
        if not self.failures:
            return ABSENT
        return f"{self.unclassified}/{self.failures} ({self.unclassified / self.failures:.0%})"


def taxonomy_breakdown(results: Sequence[TaskResult]) -> TaxonomyBreakdown:
    """Count every applicable class, and count the failures that matched nothing."""
    failures = [result for result in results if result.findings]
    counts = count_classes(failures)
    blame: dict[str, int] = {}
    for result in failures:
        for finding in result.findings:
            blame[finding.blame] = blame.get(finding.blame, 0) + 1
    return TaxonomyBreakdown(
        counts=counts,
        failures=len(failures),
        unclassified=counts[FailureClass.UNCLASSIFIED],
        blame=blame,
    )


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TaskRow:
    """One task's line in the report. Nothing here varies between identical runs.

    Notably absent: the workspace path. It is a temp directory, so including it would
    make two identical runs produce different reports and destroy the one property
    that makes a report diffable.
    """

    task_id: str
    category: str
    status: str
    turns: int = 0
    tokens: str = ABSENT
    cost: str = ABSENT
    wall_seconds: float = 0.0
    verify_exit: int | None = None
    verify_output: str = ""
    classes: tuple[FailureClass, ...] = ()
    findings: tuple[Finding, ...] = ()
    detail: str = ""


def _status(result: TaskResult) -> str:
    record = result.record
    if record.skipped:
        return "skip"
    if record.passed:
        return "pass"
    if record.timed_out:
        return "timeout"
    if record.failed_run:
        return "error"
    if record.unverifiable:
        return "unverifiable"
    return "fail"


def _row(result: TaskResult) -> TaskRow:
    record = result.record
    clamped = clamp(
        record.verify_after.output, budget=OutputBudget(remaining_chars=ROW_OUTPUT_CHARS)
    )
    detail = record.skip_reason or record.failed_run or record.verify_after.detail
    return TaskRow(
        task_id=record.task_id,
        category=record.category,
        status=_status(result),
        turns=record.turns_used,
        tokens=record.usage.render_tokens(),
        cost=record.usage.render_cost(),
        wall_seconds=_round(record.wall_seconds),
        verify_exit=record.verify_after.exit_code if record.verify_after.ran else None,
        verify_output=clamped.text,
        classes=result.classes,
        findings=result.findings,
        detail=detail,
    )


@dataclass(frozen=True, slots=True)
class RunReport:
    """One run of the suite, as a value. Every renderer is a pure function over this."""

    label: str = ""
    model: str = ""
    suite_root: str = ""
    rows: tuple[TaskRow, ...] = ()
    results: tuple[TaskResult, ...] = ()
    overall: Aggregate = Aggregate()
    per_category: Mapping[str, Aggregate] = field(default_factory=dict)
    taxonomy: TaxonomyBreakdown = field(default_factory=TaxonomyBreakdown)
    distributions: Mapping[str, Distribution] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """What a scoreboard column is headed with."""
        return self.label or self.model or "run"


def build_report(
    results: Sequence[TaskResult],
    *,
    label: str = "",
    model: str = "",
    suite_root: str = "",
) -> RunReport:
    """Fold results into a report, ordered by ``(category, task_id)``.

    The ordering is the contract that makes two runs comparable: results arrive in
    completion order, which under ``--parallel 8`` is whatever the scheduler felt
    like, and a report in that order cannot be diffed against anything.
    """
    ordered = tuple(sorted(results, key=lambda item: (item.category, item.task_id)))
    categories = sorted({result.category for result in ordered})
    ran = [result for result in ordered if not result.skipped]

    turns = [float(result.record.turns_used) for result in ran]
    walls = [float(result.record.wall_seconds) for result in ran]
    reported = [result for result in ran if result.record.usage.reported]
    tokens = [float(result.record.usage.total_tokens) for result in reported]
    priced = [result for result in reported if result.record.usage.priced]
    costs = [float(result.record.usage.cost_usd) for result in priced]

    distributions = {
        "turns": distribution("turns", turns),
        "tokens": distribution("tokens", tokens, absent=len(ran) - len(reported)),
        "cost_usd": distribution("cost_usd", costs, absent=len(ran) - len(priced)),
        "wall_seconds": distribution("wall_seconds", walls),
    }

    return RunReport(
        label=label,
        model=model,
        suite_root=suite_root,
        rows=tuple(_row(result) for result in ordered),
        results=ordered,
        overall=aggregate(ordered),
        per_category={
            category: aggregate([result for result in ordered if result.category == category])
            for category in categories
        },
        taxonomy=taxonomy_breakdown(ordered),
        distributions=distributions,
    )


# --------------------------------------------------------------------------- #
# JSON — the machine record
# --------------------------------------------------------------------------- #


def _aggregate_json(value: Aggregate) -> dict[str, Any]:
    return {
        "tasks": value.tasks,
        "attempted": value.attempted,
        "passed": value.passed,
        "failed": value.failed,
        "skipped": value.skipped,
        "unverifiable": value.unverifiable,
        "errored": value.errored,
        "timed_out": value.timed_out,
        # null, not 0.0: an empty set has no pass rate.
        "pass_rate": None if value.pass_rate is None else _round(value.pass_rate),
    }


def _distribution_json(value: Distribution) -> dict[str, Any]:
    return {
        "count": value.count,
        "absent": value.absent,
        "total": value.total,
        "min": value.minimum,
        "median": value.median,
        "mean": value.mean,
        "p90": value.p90,
        "max": value.maximum,
    }


def report_payload(report: RunReport, *, include_timings: bool = True) -> dict[str, Any]:
    """The report as plain data, in a fixed key order.

    ``include_timings=False`` drops wall-clock fields, which is how two runs on
    different hardware are compared for *behaviour*: everything else here is a
    function of the model's choices, and timings are a function of the machine.
    """
    payload: dict[str, Any] = {
        "schema": "ronin.evals.report/1",
        "label": report.label,
        "model": report.model,
        "suite_root": report.suite_root,
        "overall": _aggregate_json(report.overall),
        "per_category": {
            category: _aggregate_json(value)
            for category, value in sorted(report.per_category.items())
        },
        "taxonomy": {
            "failures": report.taxonomy.failures,
            "classified": report.taxonomy.classified,
            "unclassified": report.taxonomy.unclassified,
            "counts": {
                member.value: report.taxonomy.counts.get(member, 0) for member in FailureClass
            },
            "blame": {
                key: report.taxonomy.blame.get(key, 0) for key in sorted(report.taxonomy.blame)
            },
        },
        "distributions": {
            name: _distribution_json(report.distributions[name])
            for name in DISTRIBUTION_NAMES
            if name in report.distributions
        },
        "tasks": [],
    }
    if not include_timings:
        payload["distributions"].pop("wall_seconds", None)

    rows: list[dict[str, Any]] = []
    for row in report.rows:
        entry: dict[str, Any] = {
            "id": row.task_id,
            "category": row.category,
            "status": row.status,
            "turns": row.turns,
            "tokens": row.tokens,
            "cost": row.cost,
            "verify_exit": row.verify_exit,
            "classes": [member.value for member in row.classes],
            "findings": [
                {
                    "class": finding.failure_class.value,
                    "blame": finding.blame,
                    "rank": finding.rank,
                    "evidence": finding.evidence,
                }
                for finding in row.findings
            ],
            "detail": row.detail,
            "verify_output": row.verify_output,
        }
        if include_timings:
            entry["wall_seconds"] = row.wall_seconds
        rows.append(entry)
    payload["tasks"] = rows
    return payload


def report_json(report: RunReport, *, include_timings: bool = True, indent: int = 2) -> str:
    """The machine record. Key order is fixed by construction, so two runs diff cleanly."""
    return json.dumps(
        report_payload(report, include_timings=include_timings),
        indent=indent,
        sort_keys=False,
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------- #
# Markdown — for a human
# --------------------------------------------------------------------------- #


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines.extend("| " + " | ".join(cells) + " |" for cells in rows)
    return lines


def report_markdown(report: RunReport, *, include_timings: bool = True) -> str:
    """A human-readable report. Prints ``—`` where a number does not exist."""
    lines: list[str] = [f"# eval report — {report.name}", ""]
    if report.model:
        lines.append(f"- model: `{report.model}`")
    if report.suite_root:
        lines.append(f"- suite: `{report.suite_root}`")
    overall = report.overall
    lines.extend(
        [
            f"- tasks: {overall.tasks} ({overall.attempted} attempted, {overall.skipped} skipped)",
            f"- pass rate: {overall.render_pass_rate()} ({overall.passed}/{overall.attempted})",
            f"- unverifiable (no verify.sh): {overall.unverifiable}",
            f"- unclassified failures: {report.taxonomy.render_unclassified()}",
            "",
            "## per category",
            "",
        ]
    )
    lines.extend(
        _table(
            ("category", "pass rate", "passed", "attempted", "skipped"),
            [
                (
                    category,
                    value.render_pass_rate(),
                    str(value.passed),
                    str(value.attempted),
                    str(value.skipped),
                )
                for category, value in sorted(report.per_category.items())
            ],
        )
    )

    lines.extend(["", "## distributions", ""])
    dist_rows: list[tuple[str, str, str, str]] = []
    for name in DISTRIBUTION_NAMES:
        value = report.distributions.get(name)
        if value is None or (name == "wall_seconds" and not include_timings):
            continue
        precision = 4 if name == "cost_usd" else 1
        dist_rows.append(
            (
                name,
                str(value.count),
                str(value.absent) if value.absent else "0",
                value.render(precision=precision),
            )
        )
    lines.extend(_table(("metric", "measured", "not reported", "spread"), dist_rows))

    lines.extend(["", "## failure taxonomy", ""])
    lines.extend(
        _table(
            ("class", "fix", "failures"),
            [
                (member.value, BLAME[member], str(report.taxonomy.counts.get(member, 0)))
                for member in FailureClass
            ],
        )
    )
    lines.extend(
        [
            "",
            f"A failure may carry more than one class, so the column sums to more than "
            f"{report.taxonomy.failures} failure(s). "
            f"`unclassified` is the taxonomy's own error bar: "
            f"{report.taxonomy.render_unclassified()}.",
            "",
            "## tasks",
            "",
        ]
    )
    header = ["id", "category", "status", "turns", "tokens", "cost"]
    if include_timings:
        header.append("wall s")
    header.append("classes")
    task_rows: list[list[str]] = []
    for row in report.rows:
        cells = [
            f"`{row.task_id}`",
            row.category,
            row.status,
            str(row.turns),
            row.tokens,
            row.cost,
        ]
        if include_timings:
            cells.append(f"{row.wall_seconds:.1f}")
        cells.append(", ".join(member.value for member in row.classes) or ABSENT)
        task_rows.append(cells)
    lines.extend(_table(header, task_rows))

    failing = [row for row in report.rows if row.findings]
    if failing:
        lines.extend(["", "## evidence", ""])
        for row in failing:
            lines.append(f"### `{row.task_id}` — {row.status}")
            if row.detail:
                lines.append(f"- {row.detail}")
            lines.extend(
                f"- **{finding.failure_class.value}** (fix the {finding.blame}): {finding.evidence}"
                for finding in row.findings
            )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Scoreboard — comparing two or more runs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ScoreboardRow:
    """One task across every run being compared. ``outcomes`` is aligned to ``labels``."""

    task_id: str
    category: str
    outcomes: tuple[str, ...] = ()

    @property
    def moved(self) -> bool:
        return len(set(self.outcomes)) > 1


@dataclass(frozen=True, slots=True)
class Scoreboard:
    """Two or more runs, side by side. A pure value — no rendering, no I/O.

    ``regressions`` and ``fixes`` are relative to the **first** run, which is the
    baseline by convention: a duel and an adapter-vs-base comparison both have one
    obvious "before", and making the caller say which would invite disagreement about
    the sign of every number in the table.
    """

    labels: tuple[str, ...] = ()
    rows: tuple[ScoreboardRow, ...] = ()
    aggregates: tuple[Aggregate, ...] = ()
    taxonomies: tuple[TaxonomyBreakdown, ...] = ()
    regressions: tuple[str, ...] = ()
    fixes: tuple[str, ...] = ()
    #: Task ids present in some runs and not others. A comparison over a shifting
    #: task set is not a comparison, so it is named rather than silently intersected.
    incomparable: tuple[str, ...] = ()


#: What a scoreboard cell says when a run does not contain the task at all.
MISSING_CELL: Final = "n/a"


def scoreboard(reports: Sequence[RunReport]) -> Scoreboard:
    """Compare two or more reports. Pure over the report objects.

    Callable by anything holding reports — the duel, an adapter-vs-base comparison, a
    nightly trend — which is why it takes values and returns a value.
    """
    if not reports:
        return Scoreboard()
    labels = tuple(report.name for report in reports)
    by_run: list[dict[str, TaskRow]] = [
        {entry.task_id: entry for entry in report.rows} for report in reports
    ]
    categories: dict[str, str] = {}
    for run in by_run:
        for task_id, entry in run.items():
            categories.setdefault(task_id, entry.category)

    every = set().union(*(set(run) for run in by_run)) if by_run else set()
    shared = set(by_run[0]).intersection(*(set(run) for run in by_run[1:])) if by_run else set()

    rows: list[ScoreboardRow] = []
    for task_id in sorted(every, key=lambda name: (categories.get(name, ""), name)):
        rows.append(
            ScoreboardRow(
                task_id=task_id,
                category=categories.get(task_id, ""),
                outcomes=tuple(
                    run[task_id].status if task_id in run else MISSING_CELL for run in by_run
                ),
            )
        )

    regressions: list[str] = []
    fixes: list[str] = []
    for row in rows:
        if row.task_id not in shared:
            continue
        base = row.outcomes[0]
        later = row.outcomes[1:]
        if base == "pass" and any(outcome != "pass" for outcome in later):
            regressions.append(row.task_id)
        elif base != "pass" and any(outcome == "pass" for outcome in later):
            fixes.append(row.task_id)

    return Scoreboard(
        labels=labels,
        rows=tuple(rows),
        aggregates=tuple(report.overall for report in reports),
        taxonomies=tuple(report.taxonomy for report in reports),
        regressions=tuple(sorted(regressions)),
        fixes=tuple(sorted(fixes)),
        incomparable=tuple(sorted(every - shared)),
    )


def scoreboard_markdown(board: Scoreboard) -> str:
    """Render a scoreboard. Pure; the numbers all come from the reports."""
    if not board.labels:
        return "# scoreboard\n\nno runs to compare.\n"
    lines = ["# scoreboard", ""]
    lines.extend(
        _table(
            ("run", "pass rate", "passed", "attempted", "unclassified"),
            [
                (
                    label,
                    agg.render_pass_rate(),
                    str(agg.passed),
                    str(agg.attempted),
                    tax.render_unclassified(),
                )
                for label, agg, tax in zip(
                    board.labels, board.aggregates, board.taxonomies, strict=True
                )
            ],
        )
    )
    lines.extend(["", "## per task", ""])
    lines.extend(
        _table(
            ("id", "category", *board.labels),
            [(f"`{row.task_id}`", row.category, *row.outcomes) for row in board.rows],
        )
    )
    lines.extend(
        [
            "",
            f"- regressions vs `{board.labels[0]}`: "
            f"{', '.join(board.regressions) if board.regressions else ABSENT}",
            f"- fixes vs `{board.labels[0]}`: {', '.join(board.fixes) if board.fixes else ABSENT}",
        ]
    )
    if board.incomparable:
        lines.append(
            f"- **not in every run** (excluded from regressions/fixes): "
            f"{', '.join(board.incomparable)}"
        )
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "ABSENT",
    "DISTRIBUTION_NAMES",
    "MISSING_CELL",
    "ROW_OUTPUT_CHARS",
    "Aggregate",
    "Distribution",
    "RunReport",
    "Scoreboard",
    "ScoreboardRow",
    "TaskRow",
    "TaxonomyBreakdown",
    "aggregate",
    "build_report",
    "distribution",
    "report_json",
    "report_markdown",
    "report_payload",
    "scoreboard",
    "scoreboard_markdown",
    "taxonomy_breakdown",
]
