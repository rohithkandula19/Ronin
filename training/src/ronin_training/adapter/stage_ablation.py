"""The training-stage ablation: base -> +SFT -> +DPO -> +GRPO, one table, five metrics.

:mod:`ronin_training.adapter.threeway` answers "did the finished adapter beat base Qwen and
how far is it from Kimi" — a *model-vs-model* question. This answers the other one the roadmap
asks: **which training stage bought what.** Same held-out suite, same scoring, one row per
checkpoint in the pipeline (the untrained base, then +SFT, +DPO, +GRPO), plus Kimi as a
ceiling — and the five metrics the ablation is judged on, bound in a single table:

    pass@1 · tool-syntax validity · median turns · recovery rate · cost / task

It reuses, rather than re-implements: pass@1 is :class:`threeway.SuiteScore`'s rate, tool
validity and recovery come straight from :mod:`ronin_training.adapter.metrics`, and a stage's
raw material is a :class:`threeway.TargetRun`. The two metrics threeway does not carry —
median turns and cost per task — are the only new arithmetic here, because a stage progression
is exactly where "it passes more but takes twice as many turns and costs twice as much" has to
be visible. Pure: a :class:`StageRun` per stage in, a rendered table out; no model, no clock,
no suite copy. The real numbers come from the GPU-trained checkpoints run through
``src/ronin/evals``; a smoke run with placeholder stages produces the table *structure*, which
is the point until those checkpoints exist.
"""

from __future__ import annotations

import asyncio
import statistics
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .metrics import (
    NOT_MEASURED,
    VALID_CALL,
    Setback,
    SyntaxScore,
    TurnRecord,
    attempted_call,
    check_raw_call,
    recovery_rate,
    tool_syntax_validity,
)
from .threeway import SuiteScore, TargetRun

#: The stages, in pipeline order. ``base`` is the untrained base Qwen (the progression's
#: floor *and* the "base Qwen" baseline the spec names); ``kimi`` is a frontier ceiling, not a
#: stage — it is last and never part of the "did this stage help" arithmetic.
BASE = "base"
SFT = "sft"
DPO = "dpo"
GRPO = "grpo"
KIMI = "kimi"
STAGE_ORDER: tuple[str, ...] = (BASE, SFT, DPO, GRPO, KIMI)

#: Stages that form the training progression (Kimi excluded — it is a reference point).
PROGRESSION: tuple[str, ...] = (BASE, SFT, DPO, GRPO)

_STAGE_LABELS: Mapping[str, str] = {
    BASE: "base Qwen2.5-Coder-1.5B",
    SFT: "+SFT",
    DPO: "+DPO",
    GRPO: "+GRPO",
    KIMI: "Kimi (ceiling)",
}


class AblationError(RuntimeError):
    """The stage ablation could not be assembled."""


# --------------------------------------------------------------------------- #
# What one stage produces
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StageRun:
    """One checkpoint's raw output from the suite — the material the five metrics need.

    ``run`` is the same :class:`~ronin_training.adapter.threeway.TargetRun` the three-way
    comparison uses (its ``turns`` feed tool validity + recovery, its ``suite`` feeds pass@1),
    so a stage and a three-way column are the same on-disk object. ``task_turns`` is the turn
    count of each task (for the median a flat turn list cannot give), and ``cost_usd`` is the
    total spend across those tasks (for cost/task). Both are ``None``/empty when the harness
    did not report them, and render as ``—`` rather than a fabricated ``0``.

    ``tool_validity`` is an **override**: when the turns in ``run`` are not a raw-dialect
    sample — as with the agent-suite wiring, where ``run.turns`` are reconstructed from parsed
    ``ToolCallRecord``s for recovery and so every call already parsed — tool-syntax validity
    cannot be read off them (it would always be ``1.0``). The real wiring measures it with a
    separate decode probe and passes the rate here; :func:`stage_metrics` prefers it over the
    turn-derived value. Left ``None`` when ``run.turns`` *is* a genuine syntax sample (the
    hand-built and placeholder cases), where deriving validity from the turns is correct.
    """

    stage: str
    run: TargetRun
    task_turns: tuple[int, ...] = ()
    cost_usd: float | None = None
    tool_validity: float | None = None

    def __post_init__(self) -> None:
        if self.stage not in STAGE_ORDER:
            raise AblationError(
                f"unknown stage {self.stage!r}; the ablation is over {list(STAGE_ORDER)}"
            )
        if any(t < 0 for t in self.task_turns):
            raise AblationError("task_turns cannot be negative")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise AblationError("cost_usd cannot be negative")
        if self.tool_validity is not None and not 0.0 <= self.tool_validity <= 1.0:
            raise AblationError("tool_validity must be a rate in [0, 1]")

    @property
    def n_tasks(self) -> int:
        """The task count for the per-task denominators: the turn samples, else the suite cases."""
        if self.task_turns:
            return len(self.task_turns)
        return self.run.suite.cases if self.run.suite is not None else 0


#: Runs the suite against one checkpoint and returns its :class:`StageRun`. Async because the
#: eval suite drives a real agent; injected because the ablation must be testable with no model.
StageRunner = Callable[[], Awaitable[StageRun]]


# --------------------------------------------------------------------------- #
# The five metrics, per stage
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StageMetrics:
    """One row of the ablation: a stage and its five numbers. ``None`` == not measured."""

    stage: str
    model: str
    pass_at_1: float | None
    tool_validity: float | None
    median_turns: float | None
    recovery: float | None
    cost_per_task: float | None
    n_tasks: int
    notes: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return _STAGE_LABELS.get(self.stage, self.stage)


def stage_metrics(run: StageRun) -> StageMetrics:
    """Compute the five metrics for one stage. Pure arithmetic over the run's raw material."""
    suite = run.run.suite
    recovery = recovery_rate(run.run.turns)
    median = statistics.median(run.task_turns) if run.task_turns else None
    n = run.n_tasks
    cost_per_task = run.cost_usd / n if run.cost_usd is not None and n > 0 else None
    # Prefer the decode-probe override; fall back to the turns only when they are a genuine
    # raw-dialect sample (see StageRun.tool_validity).
    validity = run.tool_validity
    if validity is None:
        validity = tool_syntax_validity(run.run.turns).rate
    return StageMetrics(
        stage=run.stage,
        model=run.run.model,
        pass_at_1=suite.rate if suite is not None else None,
        tool_validity=validity,
        median_turns=median,
        recovery=recovery.rate,
        cost_per_task=cost_per_task,
        n_tasks=n,
        notes=run.run.notes,
    )


@dataclass(frozen=True, slots=True)
class AblationTable:
    """The whole ablation: one row per stage that ran, in pipeline order."""

    suite_id: str
    suite_cases: int
    rows: tuple[StageMetrics, ...] = ()
    provenance: tuple[str, ...] = field(default_factory=tuple)

    def row(self, stage: str) -> StageMetrics | None:
        for row in self.rows:
            if row.stage == stage:
                return row
        return None

    def delta_vs_base(self, stage: str, metric: str) -> float | None:
        """A progression stage's change on one metric against the base row, or ``None``.

        The whole point of an ablation: not "is +GRPO good" but "what did +GRPO *add*". Higher
        is better for every metric except ``median_turns`` and ``cost_per_task``, where lower
        is — but the raw delta is returned; the renderer decides which direction reads as a win.
        """
        base = self.row(BASE)
        here = self.row(stage)
        if base is None or here is None:
            return None
        a, b = getattr(here, metric), getattr(base, metric)
        return None if a is None or b is None else a - b


#: The five metrics, in table order: (attribute, header, "lower is better").
_METRICS: tuple[tuple[str, str, bool], ...] = (
    ("pass_at_1", "pass@1", False),
    ("tool_validity", "tool-syntax validity", False),
    ("median_turns", "median turns", True),
    ("recovery", "recovery rate", False),
    ("cost_per_task", "cost / task", True),
)


def assemble_ablation(
    *,
    suite_id: str,
    suite_cases: int,
    runs: Mapping[str, StageRun],
    provenance: Sequence[str] = (),
) -> AblationTable:
    """Turn per-stage runs into the ablation table. Pure: no I/O, no model, no clock."""
    if not suite_id:
        raise AblationError("suite_id is required — an ablation table with no suite is unanchored")
    unknown = sorted(set(runs) - set(STAGE_ORDER))
    if unknown:
        raise AblationError(f"unknown stage(s) {unknown}; the ablation is over {list(STAGE_ORDER)}")
    rows = tuple(stage_metrics(runs[stage]) for stage in STAGE_ORDER if stage in runs)
    return AblationTable(
        suite_id=suite_id,
        suite_cases=suite_cases,
        rows=rows,
        provenance=tuple(provenance),
    )


async def run_ablation(
    *,
    suite_id: str,
    suite_cases: int,
    runners: Mapping[str, StageRunner],
    provenance: Sequence[str] = (),
) -> AblationTable:
    """Run each stage's suite pass sequentially, then assemble the table.

    Sequential and failure-tolerant, exactly like :func:`threeway.run_three_way`: the
    checkpoints are local models on one machine, and a stage that fails to run becomes a noted,
    metric-less row rather than destroying the stages that already ran.
    """
    runs: dict[str, StageRun] = {}
    for stage in STAGE_ORDER:
        runner = runners.get(stage)
        if runner is None:
            continue
        try:
            runs[stage] = await runner()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a failed stage is a noted empty row, not a dead table
            runs[stage] = StageRun(
                stage=stage,
                run=TargetRun(
                    model=f"{stage} (did not run)",
                    notes=(f"stage failed to run: {type(exc).__name__}: {exc}",),
                ),
            )
    return assemble_ablation(
        suite_id=suite_id, suite_cases=suite_cases, runs=runs, provenance=provenance
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _num(value: float | None, *, places: int = 3) -> str:
    return NOT_MEASURED if value is None else f"{value:.{places}f}"


def _cost(value: float | None) -> str:
    return NOT_MEASURED if value is None else f"${value:.4f}"


def _cell(row: StageMetrics, attr: str) -> str:
    value = getattr(row, attr)
    if attr == "cost_per_task":
        return _cost(value)
    if attr == "median_turns":
        return _num(value, places=1)
    return _num(value)


def render_markdown(table: AblationTable) -> str:
    """The publishable ablation: one row per stage, five metric columns, deltas vs base.

    Every unmeasured cell is ``—``, never a number. The delta block reads each metric in its
    own direction (more pass@1 is a win; fewer turns and less cost are wins), so "passes more
    but costs more" shows up as a win and a regression in the same row rather than being hidden.
    """
    lines: list[str] = [
        "# Training-stage ablation — base → +SFT → +DPO → +GRPO",
        "",
        f"Suite: `{table.suite_id}` ({table.suite_cases} cases). Every stage ran the same "
        "held-out suite with the same scoring. Kimi is a frontier **ceiling**, not a stage.",
        "",
    ]
    if table.provenance:
        lines.append("Provenance:")
        lines.extend(f"- {item}" for item in table.provenance)
        lines.append("")

    if not table.rows:
        lines.extend(["No stage produced a result. Nothing to report.", ""])
        return "\n".join(lines)

    header = "| stage | " + " | ".join(h for _, h, _ in _METRICS) + " | n |"
    divider = "|---" * (len(_METRICS) + 2) + "|"
    lines.extend([header, divider])
    for row in table.rows:
        cells = " | ".join(_cell(row, attr) for attr, _, _ in _METRICS)
        lines.append(f"| **{row.label}** | {cells} | {row.n_tasks} |")
    lines.append("")

    lines.extend(_render_deltas(table))
    lines.extend(_render_definitions())
    lines.extend(_render_notes(table))
    lines.extend(
        [
            "## Honesty",
            "",
            f"`{NOT_MEASURED}` means **not measured** — no run produced that number; it is never "
            "rendered as `0`. A stage that did not run has a noted, metric-less row rather than a "
            "zero-filled one. The real numbers come from the GPU-trained checkpoints; a smoke run "
            "produces this structure with placeholder stages.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_deltas(table: AblationTable) -> list[str]:
    if table.row(BASE) is None:
        return [
            "## What each stage added",
            "",
            f"{NOT_MEASURED} — the base row did not run, so there is nothing to measure the "
            "progression against.",
            "",
        ]
    lines = [
        "## What each stage added (Δ vs base, in each metric's own direction)",
        "",
        "| stage | " + " | ".join(h for _, h, _ in _METRICS) + " |",
        "|---" * (len(_METRICS) + 1) + "|",
    ]
    for stage in (SFT, DPO, GRPO):
        if table.row(stage) is None:
            continue
        cells = []
        for attr, _, lower_is_better in _METRICS:
            delta = table.delta_vs_base(stage, attr)
            cells.append(_signed_delta(delta, lower_is_better=lower_is_better))
        lines.append(f"| **{_STAGE_LABELS[stage]}** | " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def _signed_delta(delta: float | None, *, lower_is_better: bool) -> str:
    if delta is None:
        return NOT_MEASURED
    mark = "" if delta == 0 else ("✓" if (delta < 0) == lower_is_better else "✗")
    return f"{delta:+.3f} {mark}".strip()


def _render_definitions() -> list[str]:
    return [
        "## What the five metrics mean",
        "",
        "- **pass@1** — suite pass rate at one sample per task (passed / cases).",
        "- **tool-syntax validity** — of turns that attempted a tool call, the fraction whose "
        "call parses, names a registered tool, and validates against its schema.",
        "- **median turns** — the median number of turns a task took; lower is better at equal "
        "pass@1 (a model that solves in fewer turns is cheaper and steadier).",
        "- **recovery rate** — of turns right after a setback (failed `ToolResult` or approval "
        "denial), the fraction that did not repeat the setback's action.",
        "- **cost / task** — total USD spend across the run divided by the task count; lower is "
        "better. `—` when the harness did not report cost.",
        "",
    ]


def _render_notes(table: AblationTable) -> list[str]:
    noted = [row for row in table.rows if row.notes]
    if not noted:
        return []
    lines = ["## Notes", ""]
    for row in noted:
        for note in row.notes:
            lines.append(f"- **{row.label}**: {note}")
    lines.append("")
    return lines


def write_report(table: AblationTable, path: str | Path) -> Path:
    """Write :func:`render_markdown` to ``path`` with ``\\n`` endings on every platform."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        handle.write(render_markdown(table))
    return out


# --------------------------------------------------------------------------- #
# Wiring the real suite in
# --------------------------------------------------------------------------- #
#
# The five metrics come from two probes against the *same* checkpoint, because the agent
# loop and the raw decoder answer different questions and only one of them is the agent:
#
# * the **agent suite** (`ronin.evals.run_suite`) gives pass@1, median turns, cost/task and
#   recovery. Recovery is reconstructed from each task's parsed `ToolCallRecord`s — a failed
#   call is a setback, the next call recovers unless it repeats the fingerprint — which is an
#   exact fit for `metrics.recovery_rate`'s model.
# * the **decode probe** gives tool-syntax validity, which the agent loop cannot: the format
#   shim parses and repairs `<ronin:tool_call>` *before* the loop emits an event, so a report
#   only ever holds calls that already parsed. Validity is a property of the model's raw
#   output, so it is measured over raw completions on the held-out prompts and passed in as
#   `StageRun.tool_validity`.
#
# Every reducer is duck-typed over the report's shape rather than importing `ronin.evals`
# (which `ronin_training` does not depend on), exactly as `threeway.suite_score_of` is: a
# wrong attribute name surfaces here as a named `AblationError`, not as an `AttributeError`
# in the middle of a five-stage run.

#: Runs the raw-completion decode probe and hands back its syntax score. Injected and async
#: for the same reason the suite runner is: this module must be testable with no model.
ValidityProbe = Callable[[], Awaitable["SyntaxScore | None"]]

#: One raw model completion for one held-out prompt. Only ``.text`` is read.
CompletionProvider = Callable[[Mapping[str, object]], object]


def _record(result: object) -> object:
    """The per-task outcome, whatever the result calls it (``.record`` or ``.outcome``)."""
    for attr in ("record", "outcome"):
        rec = getattr(result, attr, None)
        if rec is not None:
            return rec
    raise AblationError(
        f"result {type(result).__name__} exposes neither .record nor .outcome; cannot read "
        "the tool calls a stage's recovery rate is measured from"
    )


def _ran_results(report: object) -> list[object]:
    results = getattr(report, "results", None)
    if results is None:
        raise AblationError(
            f"report {type(report).__name__} has no .results; pass a ronin.evals RunReport "
            "or a value that duck-types as one"
        )
    return [r for r in results if not getattr(r, "skipped", False)]


def suite_score_from_report(report: object) -> SuiteScore:
    """pass@1's raw material: passes over *attempted* (skips excluded, matching the suite)."""
    overall = getattr(report, "overall", None)
    passed = getattr(overall, "passed", None)
    attempted = getattr(overall, "attempted", None)
    if not isinstance(passed, int) or not isinstance(attempted, int):
        raise AblationError(
            f"report.overall must expose int .passed and .attempted; got {type(overall).__name__}"
        )
    return SuiteScore(cases=attempted, passed=passed)


def task_turns_from_report(report: object) -> tuple[int, ...]:
    """One turn count per ran task — the sample the median is taken over."""
    turns: list[int] = []
    for result in _ran_results(report):
        used = getattr(_record(result), "turns_used", None)
        if isinstance(used, int):
            turns.append(used)
    return tuple(turns)


def cost_from_report(report: object) -> float | None:
    """Total USD spend across the run, or ``None`` when nothing was priced."""
    dists = getattr(report, "distributions", None)
    dist = dists.get("cost_usd") if isinstance(dists, Mapping) else None
    if dist is None:
        return None
    count = getattr(dist, "count", 0)
    total = getattr(dist, "total", None)
    if not count or total is None:
        return None
    return float(total)


def recovery_turns_from_outcome(outcome: object, *, task_id: str) -> list[TurnRecord]:
    """Reconstruct a task's recovery-relevant turns from its parsed ``ToolCallRecord``s.

    A call whose result was ``ok=False`` is a setback; the call that follows it recovers
    unless it repeats the same fingerprint. The trailing case — a failure with nothing after
    it — is deliberately *not* counted as recovery: the agent stopping after a failure is not
    evidence it adapted, and inventing an opportunity there would inflate the rate.
    """
    records: list[TurnRecord] = []
    prev_fail_fp: str | None = None
    for index, call in enumerate(getattr(outcome, "tool_calls", ()) or ()):
        fp = getattr(call, "fingerprint", "") or f"{getattr(call, 'name', '?')}:{index}"
        ok = bool(getattr(call, "ok", True))
        if prev_fail_fp is not None:
            records.append(
                TurnRecord(
                    task_id=task_id,
                    call=VALID_CALL,
                    fingerprint=fp,
                    setback=Setback.FAILED_RESULT,
                    setback_fingerprint=prev_fail_fp,
                )
            )
        else:
            records.append(TurnRecord(task_id=task_id, call=VALID_CALL, fingerprint=fp))
        prev_fail_fp = fp if not ok else None
    return records


def stage_run_from_report(
    stage: str, report: object, *, tool_validity: float | None = None
) -> StageRun:
    """Reduce a ``ronin.evals`` ``RunReport`` to a :class:`StageRun`. Pure over the report.

    Gives four of the five metrics; ``tool_validity`` comes from the decode probe and is
    passed through. The reconstructed ``run.turns`` are a recovery sample, not a syntax one,
    so validity is *only* read from them when no override is supplied (never in the real
    wiring, where :func:`stage_runner` always runs the probe).
    """
    model = getattr(report, "model", "") or stage
    turns: list[TurnRecord] = []
    for result in _ran_results(report):
        task_id = str(getattr(result, "task_id", "") or "task")
        turns.extend(recovery_turns_from_outcome(_record(result), task_id=task_id))
    return StageRun(
        stage=stage,
        run=TargetRun(model=model, turns=tuple(turns), suite=suite_score_from_report(report)),
        task_turns=task_turns_from_report(report),
        cost_usd=cost_from_report(report),
        tool_validity=tool_validity,
    )


def syntax_probe(
    cases: Sequence[Mapping[str, object]],
    provider: CompletionProvider,
    *,
    schemas: Mapping[str, Mapping[str, object]],
) -> SyntaxScore:
    """Score tool-syntax validity over raw completions on the held-out prompts.

    Runs each case through ``provider`` (one raw completion, no agent loop), classifies its
    output with the same :func:`~ronin_training.adapter.metrics.check_raw_call` the placeholder
    and three-way report use, and returns the resulting ``SyntaxScore``. Pure over the injected
    provider, so a test scores hand-written completions with no model on the box.
    """
    turns: list[TurnRecord] = []
    for index, case in enumerate(cases):
        resp = provider(case)
        text = str(getattr(resp, "text", "") or "")
        if attempted_call(text):
            check = check_raw_call(text, schemas=schemas)
            turns.append(
                TurnRecord(task_id=f"probe-{index}", call=check, fingerprint=f"probe-{index}")
            )
        else:
            turns.append(TurnRecord(task_id=f"probe-{index}"))
    return tool_syntax_validity(turns)


def stage_runner(
    *,
    stage: str,
    run_suite: Callable[[], Awaitable[object]],
    validity_probe: ValidityProbe | None = None,
) -> StageRunner:
    """Compose a real :data:`StageRunner` from the suite's own pieces.

    ``run_suite`` is ``ronin.evals.run_suite`` bound to this stage's checkpoint (its tasks,
    its adapter, its config); ``validity_probe`` is :func:`syntax_probe` bound to the same
    checkpoint's raw decoder. Keeping both injected is what lets this wiring be finished,
    tested and reviewed before a checkpoint exists — the same seam :func:`threeway.target_runner`
    uses. A probe that returns ``None`` (or is omitted) leaves validity unmeasured rather than
    guessed.
    """

    async def run() -> StageRun:
        report = await run_suite()
        validity: float | None = None
        if validity_probe is not None:
            score = await validity_probe()
            validity = getattr(score, "rate", None) if score is not None else None
        return stage_run_from_report(stage, report, tool_validity=validity)

    return run


def placeholder_table() -> AblationTable:
    """A tiny, model-free ablation with every stage present — the smoke-run structure.

    What ``run_ablation`` would produce once the checkpoints exist, but built from fixed
    numbers so `python -m` and a test can show the table *shape* — five metric columns, a row
    per stage, the delta block — before a single GPU has run. The numbers are illustrative and
    say so in the provenance.
    """
    from .metrics import CallCheck

    bad = CallCheck(ok=False, reason="schema mismatch")

    def turns(valid: int, invalid: int, recovered: int, repeated: int) -> tuple[TurnRecord, ...]:
        """`valid`/`invalid` classified calls (for tool validity) + setback turns (for recovery)."""
        rows: list[TurnRecord] = []
        for k in range(valid):
            rows.append(TurnRecord(task_id="t", call=VALID_CALL, fingerprint=f"ok{k}"))
        for k in range(invalid):
            rows.append(TurnRecord(task_id="t", call=bad, fingerprint=f"bad{k}"))
        for _ in range(recovered):  # responded differently after a failure — recovered
            rows.append(
                TurnRecord(task_id="t", setback=Setback.FAILED_RESULT, setback_fingerprint="old")
            )
        for _ in range(repeated):  # re-issued the exact failing call — did not recover
            rows.append(
                TurnRecord(
                    task_id="t",
                    call=VALID_CALL,
                    fingerprint="old",
                    setback=Setback.FAILED_RESULT,
                    setback_fingerprint="old",
                )
            )
        return tuple(rows)

    def task_turns(center: int) -> tuple[int, ...]:
        """20 per-task turn counts (one per suite case) spread around ``center``."""
        return tuple(center + (i % 3) - 1 for i in range(20))

    stages = {
        BASE: StageRun(
            BASE,
            TargetRun("qwen2.5-coder-1.5b", turns(4, 6, 2, 3), SuiteScore(20, 6)),
            task_turns(9),
            0.30,
        ),
        SFT: StageRun(
            SFT, TargetRun("+sft", turns(7, 3, 4, 2), SuiteScore(20, 9)), task_turns(8), 0.28
        ),
        DPO: StageRun(
            DPO, TargetRun("+dpo", turns(8, 2, 5, 1), SuiteScore(20, 11)), task_turns(7), 0.26
        ),
        GRPO: StageRun(
            GRPO, TargetRun("+grpo", turns(9, 1, 6, 1), SuiteScore(20, 13)), task_turns(6), 0.24
        ),
        KIMI: StageRun(
            KIMI, TargetRun("kimi", turns(10, 0, 7, 0), SuiteScore(20, 17)), task_turns(5), 4.00
        ),
    }
    return assemble_ablation(
        suite_id="phase-11-holdout",
        suite_cases=20,
        runs=stages,
        provenance=("PLACEHOLDER numbers — illustrative structure, not a measured run",),
    )


__all__ = [
    "BASE",
    "DPO",
    "GRPO",
    "KIMI",
    "PROGRESSION",
    "SFT",
    "STAGE_ORDER",
    "AblationError",
    "AblationTable",
    "CompletionProvider",
    "StageMetrics",
    "StageRun",
    "StageRunner",
    "ValidityProbe",
    "assemble_ablation",
    "cost_from_report",
    "placeholder_table",
    "recovery_turns_from_outcome",
    "render_markdown",
    "run_ablation",
    "stage_metrics",
    "stage_run_from_report",
    "stage_runner",
    "suite_score_from_report",
    "syntax_probe",
    "task_turns_from_report",
    "write_report",
]
