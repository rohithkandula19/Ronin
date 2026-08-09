"""The three-way evaluation: adapter vs base Qwen vs Kimi, on the same phase-11 suite.

The question this answers is the only one that matters about the fine-tune: *did it
help?* Against the base model it started from, and — for scale — against a frontier
model, on the identical task suite, with the identical scoring.

Two design commitments make the answer trustworthy:

1. **The suite is not reimplemented here.** ``src/ronin/evals`` owns the task suite,
   the ``RunAgent`` seam and the scoreboard. This module takes one
   :data:`TargetRunner` per target — a callable that runs that suite against one
   model and hands back a :class:`TargetRun` — and does nothing but arithmetic and
   rendering over the results. A second copy of the suite would let the three
   columns be scored differently, which is the one bug that would make the whole
   comparison worthless.
2. **"The adapter lost" is a first-class outcome, not an error path.**
   :class:`Outcome` has an ``ADAPTER_LOSES`` member, :func:`render_markdown` gives
   it a proper section with the deltas and a concrete iterate list, and the tests
   assert that it renders legibly. If a negative result rendered badly, nobody
   would publish one — and an honest negative result is a better signal than a
   vague positive one.

Nothing here invents a number. A rate with no denominator renders as ``—``, a target
that was not run is absent from the table rather than zero-filled, and the verdict
refuses to declare a winner when either metric was not measured.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .metrics import (
    NOT_MEASURED,
    RecoveryScore,
    SyntaxScore,
    TurnRecord,
    recovery_rate,
    tool_syntax_validity,
)

#: The three columns, in the order they are reported. ``adapter`` first because it
#: is the thing under test; ``base`` second because it is the comparison that
#: decides the verdict; ``kimi`` last because it is a ceiling, not a rival.
ADAPTER = "adapter"
BASE = "base"
KIMI = "kimi"
TARGET_ORDER: tuple[str, ...] = (ADAPTER, BASE, KIMI)

_TARGET_LABELS: Mapping[str, str] = {
    ADAPTER: "ronin-adapter-qwen1.5b",
    BASE: "base Qwen2.5-Coder-1.5B",
    KIMI: "Kimi (reference ceiling)",
}


class ThreeWayError(RuntimeError):
    """The three-way run could not be assembled."""


# --------------------------------------------------------------------------- #
# What one target produces
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SuiteScore:
    """The phase-11 suite's headline, reduced to the two numbers the table shows.

    Kept as a tiny value rather than holding the suite's own report object so this
    module does not have to change when that object does — and so a fake in a test
    is three lines.
    """

    cases: int
    passed: int

    def __post_init__(self) -> None:
        if self.cases < 0 or self.passed < 0:
            raise ThreeWayError("SuiteScore counts must be >= 0")
        if self.passed > self.cases:
            raise ThreeWayError(
                f"impossible SuiteScore: {self.passed} passed of {self.cases} cases"
            )

    @property
    def rate(self) -> float | None:
        return self.passed / self.cases if self.cases else None

    def render(self) -> str:
        if self.rate is None:
            return f"{NOT_MEASURED} (suite reported 0 cases)"
        return f"{self.passed}/{self.cases} = {self.rate:.3f}"


def suite_score_of(scoreboard: object) -> SuiteScore:
    """Reduce ``src/ronin/evals``' scoreboard object to a :class:`SuiteScore`.

    Duck-typed across the plausible attribute names rather than pinned to one:
    this module and the eval suite are written in parallel, and a wrong guess
    should surface as a named error here instead of an ``AttributeError`` in the
    middle of a three-model run.
    """
    for cases_attr, passed_attr in (
        ("cases", "passed"),
        ("total", "passed"),
        ("total", "passed_cases"),
        ("case_count", "pass_count"),
    ):
        cases = getattr(scoreboard, cases_attr, None)
        passed = getattr(scoreboard, passed_attr, None)
        if isinstance(cases, int) and isinstance(passed, int):
            return SuiteScore(cases=cases, passed=passed)
    raise ThreeWayError(
        f"cannot read a case count and a pass count off {type(scoreboard).__name__}; "
        "pass a SuiteScore directly, or teach suite_score_of() its attribute names"
    )


@dataclass(frozen=True, slots=True)
class TargetRun:
    """One model's raw output from the suite."""

    model: str
    turns: tuple[TurnRecord, ...] = ()
    #: ``None`` when the suite's own scoreboard was not produced (e.g. the harness
    #: ran only the turn-level probes). Absent, never zero.
    suite: SuiteScore | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.model:
            raise ThreeWayError(
                "TargetRun.model must name the model that actually ran — a report "
                "column with no model behind it is unattributable"
            )


#: Runs the phase-11 suite against one model. Async because the eval suite drives a
#: real agent; injected because this module must be testable with no model at all.
TargetRunner = Callable[[], Awaitable[TargetRun]]


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TargetResult:
    """One column of the three-way table."""

    name: str
    model: str
    syntax: SyntaxScore
    recovery: RecoveryScore
    suite: SuiteScore | None = None
    notes: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return _TARGET_LABELS.get(self.name, self.name)


class Outcome(StrEnum):
    """What the comparison concluded.

    ``ADAPTER_LOSES`` and ``MIXED`` are members, not exceptions. Ties count as
    losing: "did not beat base" is the claim being tested, and a tie does not beat
    anything.
    """

    NOT_RUN = "not_run"
    ADAPTER_WINS = "adapter_wins"
    ADAPTER_LOSES = "adapter_loses"
    MIXED = "mixed"


#: What to do when the adapter did not beat the base model. Prose, not numbers —
#: these are the levers, in the order they are worth pulling.
ITERATE_STEPS: tuple[str, ...] = (
    "check the dialect first: the adapter must emit <ronin:tool_call>, which is NOT "
    "the bare <tool_call> that packages/dialect uses. A tag mismatch reads as a total "
    "syntax failure and is a data bug, not a training one.",
    "read the invalid-call reasons in the report: a schema violation is a training "
    "problem, an unknown tool name is a prompt problem, a missing close tag is a "
    "decoding problem. They need different fixes.",
    "confirm the holdout was split by task (split_manifest.json, leaked_tasks empty). "
    "A leaked split can make a *worse* adapter look better and hide a real regression.",
    "if recovery lost but syntax won, the SFT corpus is short of setback→adaptation "
    "transitions; that is what the DPO preference pairs are for.",
    "if both lost, publish the negative result with this report attached rather than "
    "retraining on the same data with a different seed.",
)


@dataclass(frozen=True, slots=True)
class Verdict:
    """The comparison's conclusion, with the deltas that produced it.

    Deltas are ``float | None``: ``None`` means one of the two rates had no
    denominator, and the verdict is then :attr:`Outcome.NOT_RUN` rather than a
    guess in either direction.
    """

    outcome: Outcome
    headline: str
    syntax_delta: float | None = None
    recovery_delta: float | None = None
    next_steps: tuple[str, ...] = ()

    @property
    def adapter_beat_base(self) -> bool:
        return self.outcome is Outcome.ADAPTER_WINS


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


@dataclass(frozen=True, slots=True)
class ThreeWayReport:
    """The whole comparison: three columns, two first-class metrics, one verdict."""

    suite_id: str
    suite_cases: int
    results: tuple[TargetResult, ...] = ()
    #: Free-text provenance (commit, date, machine). Passed in rather than read
    #: from the clock so a rendered report is byte-stable in a test.
    provenance: tuple[str, ...] = field(default_factory=tuple)

    def target(self, name: str) -> TargetResult | None:
        for result in self.results:
            if result.name == name:
                return result
        return None

    def verdict(self) -> Verdict:
        adapter = self.target(ADAPTER)
        base = self.target(BASE)
        if adapter is None or base is None:
            missing = [
                name for name, value in ((ADAPTER, adapter), (BASE, base)) if value is None
            ]
            return Verdict(
                outcome=Outcome.NOT_RUN,
                headline=(
                    f"not run: no result for {missing} — the adapter can only be "
                    "judged against the base model it was trained from"
                ),
            )
        syntax_delta = _delta(adapter.syntax.rate, base.syntax.rate)
        recovery_delta = _delta(adapter.recovery.rate, base.recovery.rate)
        if syntax_delta is None or recovery_delta is None:
            unmeasured = [
                name
                for name, value in (
                    ("tool-syntax validity", syntax_delta),
                    ("recovery rate", recovery_delta),
                )
                if value is None
            ]
            return Verdict(
                outcome=Outcome.NOT_RUN,
                headline=(
                    f"not run: {' and '.join(unmeasured)} had no measurements on at "
                    "least one target, so no comparison is possible"
                ),
                syntax_delta=syntax_delta,
                recovery_delta=recovery_delta,
            )

        won_syntax = syntax_delta > 0
        won_recovery = recovery_delta > 0
        if won_syntax and won_recovery:
            return Verdict(
                outcome=Outcome.ADAPTER_WINS,
                headline=(
                    "the adapter beat base Qwen on both tool-syntax validity "
                    f"({syntax_delta:+.3f}) and recovery rate ({recovery_delta:+.3f})"
                ),
                syntax_delta=syntax_delta,
                recovery_delta=recovery_delta,
            )
        if won_syntax or won_recovery:
            better = "tool-syntax validity" if won_syntax else "recovery rate"
            worse = "recovery rate" if won_syntax else "tool-syntax validity"
            return Verdict(
                outcome=Outcome.MIXED,
                headline=(
                    f"the adapter beat base Qwen on {better} but NOT on {worse} "
                    f"(syntax {syntax_delta:+.3f}, recovery {recovery_delta:+.3f}) — "
                    "a partial result, reported as one"
                ),
                syntax_delta=syntax_delta,
                recovery_delta=recovery_delta,
                next_steps=ITERATE_STEPS,
            )
        return Verdict(
            outcome=Outcome.ADAPTER_LOSES,
            headline=(
                "the adapter did NOT beat base Qwen on either first-class metric "
                f"(tool-syntax validity {syntax_delta:+.3f}, recovery rate "
                f"{recovery_delta:+.3f}). A tie counts as not beating it."
            ),
            syntax_delta=syntax_delta,
            recovery_delta=recovery_delta,
            next_steps=ITERATE_STEPS,
        )


def assemble_report(
    *,
    suite_id: str,
    suite_cases: int,
    runs: Mapping[str, TargetRun],
    provenance: Sequence[str] = (),
) -> ThreeWayReport:
    """Turn raw per-target runs into a report. Pure: no I/O, no model, no clock."""
    if not suite_id:
        raise ThreeWayError(
            "suite_id is required — a three-way table without the suite it came from "
            "cannot be compared to any other run"
        )
    unknown = sorted(set(runs) - set(TARGET_ORDER))
    if unknown:
        raise ThreeWayError(
            f"unknown target(s) {unknown}; the three-way comparison is over "
            f"{list(TARGET_ORDER)}"
        )
    results: list[TargetResult] = []
    for name in TARGET_ORDER:
        run = runs.get(name)
        if run is None:
            continue
        results.append(
            TargetResult(
                name=name,
                model=run.model,
                syntax=tool_syntax_validity(run.turns),
                recovery=recovery_rate(run.turns),
                suite=run.suite,
                notes=run.notes,
            )
        )
    return ThreeWayReport(
        suite_id=suite_id,
        suite_cases=suite_cases,
        results=tuple(results),
        provenance=tuple(provenance),
    )


async def run_three_way(
    *,
    suite_id: str,
    suite_cases: int,
    runners: Mapping[str, TargetRunner],
    provenance: Sequence[str] = (),
) -> ThreeWayReport:
    """Run every target's suite pass, then assemble the report.

    Targets run **sequentially**, not concurrently: two of the three are local
    models on one machine's memory, and running them at once would make each one's
    timing meaningless and can OOM the box. A failing target is not fatal to the
    others — its exception becomes a note on that column and the rest of the table
    still gets published, because a Kimi outage must not destroy an adapter-vs-base
    comparison that already ran.
    """
    runs: dict[str, TargetRun] = {}
    for name in TARGET_ORDER:
        runner = runners.get(name)
        if runner is None:
            continue
        try:
            runs[name] = await runner()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded as a note, see docstring
            runs[name] = TargetRun(
                model=f"{name} (did not run)",
                notes=(f"target failed to run: {type(exc).__name__}: {exc}",),
            )
    return assemble_report(
        suite_id=suite_id, suite_cases=suite_cases, runs=runs, provenance=provenance
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

METRIC_DEFINITIONS: tuple[tuple[str, str], ...] = (
    (
        "tool-syntax validity",
        "Of the assistant turns that attempted a tool call, the fraction whose call "
        "parses out of a complete `<ronin:tool_call>` block, names a tool in Ronin's "
        "registry, passes its arguments as a JSON object, and validates against that "
        "tool's JSON schema. Turns that attempted no call are excluded from the "
        "denominator and counted separately, so a model that never calls a tool "
        "cannot score 100%.",
    ),
    (
        "recovery rate",
        "Of the assistant turns immediately preceded by a setback — a failed "
        "`ToolResult`, or an approval denial from the safety layer — the fraction "
        "that did not repeat the setback's action. Repetition is measured with "
        "`ronin.core.loop.fingerprint`, the same identity the runtime's stall "
        "detector uses. Calling no tool and reporting back counts as recovery: it "
        "adapted. Re-issuing the identical fingerprint does not.",
    ),
)


def _cell(value: str | None) -> str:
    return value if value else NOT_MEASURED


def render_markdown(report: ThreeWayReport) -> str:
    """The publishable report. Every unmeasured cell is ``—``, never a number."""
    verdict = report.verdict()
    lines: list[str] = [
        "# Three-way evaluation — adapter vs base Qwen vs Kimi",
        "",
        f"Suite: `{report.suite_id}` ({report.suite_cases} cases). "
        "All three targets ran the same suite with the same scoring.",
        "",
    ]
    if report.provenance:
        lines.append("Provenance:")
        lines.extend(f"- {item}" for item in report.provenance)
        lines.append("")

    lines.extend(_render_outcome(verdict))
    lines.extend(_render_table(report))
    lines.extend(_render_definitions())
    lines.extend(_render_details(report))
    lines.extend(
        [
            "## Honesty",
            "",
            f"`{NOT_MEASURED}` means **not measured** — no run produced that number. "
            "It is never rendered as `0.0`, and a target that did not run has no "
            "column rather than a zero-filled one. Every figure above is computed "
            "from the turns of the run named in the provenance block.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_outcome(verdict: Verdict) -> list[str]:
    heading = {
        Outcome.ADAPTER_WINS: "## Outcome: the adapter beat base",
        Outcome.ADAPTER_LOSES: "## Outcome: the adapter did NOT beat base",
        Outcome.MIXED: "## Outcome: mixed — the adapter beat base on one metric of two",
        Outcome.NOT_RUN: "## Outcome: not run",
    }[verdict.outcome]
    lines = [heading, "", f"> {verdict.headline}", ""]
    if verdict.outcome in (Outcome.ADAPTER_LOSES, Outcome.MIXED):
        lines.extend(
            [
                "This is a real result and it is published as one. An honest negative "
                "result says more about a pipeline than a vague positive one, and the "
                "point of this evaluation is to stop guessing — including guessing "
                "that the fine-tune worked.",
                "",
                "| metric | adapter − base |",
                "|---|---|",
                f"| tool-syntax validity | {_signed(verdict.syntax_delta)} |",
                f"| recovery rate | {_signed(verdict.recovery_delta)} |",
                "",
                "### Iterate, in this order",
                "",
            ]
        )
        lines.extend(f"{n}. {step}" for n, step in enumerate(verdict.next_steps, start=1))
        lines.append("")
    elif verdict.outcome is Outcome.ADAPTER_WINS:
        lines.extend(
            [
                "| metric | adapter − base |",
                "|---|---|",
                f"| tool-syntax validity | {_signed(verdict.syntax_delta)} |",
                f"| recovery rate | {_signed(verdict.recovery_delta)} |",
                "",
            ]
        )
    return lines


def _signed(value: float | None) -> str:
    return NOT_MEASURED if value is None else f"{value:+.3f}"


def _render_table(report: ThreeWayReport) -> list[str]:
    if not report.results:
        return [
            "## The three numbers",
            "",
            "No target produced a result. Nothing to report.",
            "",
        ]
    header = "| metric | " + " | ".join(result.label for result in report.results) + " |"
    divider = "|---" * (len(report.results) + 1) + "|"
    rows = [
        "| **tool-syntax validity** | "
        + " | ".join(result.syntax.render() for result in report.results)
        + " |",
        "| **recovery rate** | "
        + " | ".join(result.recovery.render() for result in report.results)
        + " |",
        "| suite pass rate | "
        + " | ".join(
            result.suite.render() if result.suite is not None else NOT_MEASURED
            for result in report.results
        )
        + " |",
        "| model | "
        + " | ".join(f"`{result.model}`" for result in report.results)
        + " |",
    ]
    return [
        "## The three numbers",
        "",
        header,
        divider,
        *rows,
        "",
        "Kimi is a **reference ceiling**, not the comparison that decides the verdict. "
        "The adapter's claim is against the base model it was trained from; Kimi says "
        "how far a 1.5B model still is from a frontier one on this suite.",
        "",
    ]


def _render_definitions() -> list[str]:
    lines = ["## What the two metrics mean, exactly", ""]
    for name, definition in METRIC_DEFINITIONS:
        lines.extend([f"**{name}** — {definition}", ""])
    return lines


def _render_details(report: ThreeWayReport) -> list[str]:
    lines = ["## Per-target detail", ""]
    for result in report.results:
        syntax = result.syntax
        recovery = result.recovery
        lines.extend(
            [
                f"### {result.label} (`{result.model}`)",
                "",
                f"- turns observed: {syntax.turns} "
                f"({syntax.attempts} attempted a tool call, {syntax.no_attempt} did not)",
                f"- tool-syntax validity: {syntax.render()} "
                f"({syntax.invalid} invalid)",
                f"- recovery rate: {recovery.render()}",
            ]
        )
        for kind, recovered, total in recovery.breakdown:
            lines.append(f"  - after {kind}: {recovered}/{total}")
        if syntax.reasons:
            lines.append("- invalid-call reasons (verbatim, deduplicated):")
            for reason in sorted(set(syntax.reasons)):
                lines.append(f"  - {reason}")
        for note in result.notes:
            lines.append(f"- note: {note}")
        lines.append("")
    return lines


def write_report(report: ThreeWayReport, path: str | Path) -> Path:
    """Write :func:`render_markdown` to ``path``, with ``\\n`` endings on every platform."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        handle.write(render_markdown(report))
    return out


# --------------------------------------------------------------------------- #
# Wiring the real suite in
# --------------------------------------------------------------------------- #

#: Turns one of the eval suite's report objects into the turn records the two
#: first-class metrics consume. Injected because the suite's report shape is owned
#: by ``src/ronin/evals``, and because a fake in a test needs to supply known
#: turns without constructing one of those objects.
TurnExtractor = Callable[[Any], Sequence[TurnRecord]]


def evals_module() -> Any:
    """Import ``ronin.evals`` lazily, or fail with a named error.

    Lazy because ``ronin_training`` does not depend on ``ronin``: the configs, the
    split and the metric arithmetic are all usable without it, and only the wiring
    to the real suite needs it on the path.
    """
    try:
        import ronin.evals as module
    except ImportError as exc:
        raise ThreeWayError(
            "ronin.evals is not importable, so the phase-11 suite cannot be run: put "
            "the repo's `src` on PYTHONPATH (or `pip install -e .`). This harness "
            "deliberately does not carry its own copy of the suite — three columns "
            "scored by two implementations would not be comparable"
        ) from exc
    return module


def target_runner(
    *,
    model: str,
    run_suite: Callable[[], Awaitable[Any]],
    extract_turns: TurnExtractor,
    score: Callable[[Any], SuiteScore | None] = suite_score_of,
) -> TargetRunner:
    """Compose a :data:`TargetRunner` from the suite's own pieces.

    ``run_suite`` is whatever ``src/ronin/evals`` exposes for "run every case
    against this agent" bound to one model; ``extract_turns`` projects its reports
    into :class:`~ronin_training.adapter.metrics.TurnRecord`s; ``score`` reduces
    its scoreboard. Keeping all three injected is what lets this module be
    finished, tested and reviewed before the suite's names are final.
    """

    async def run() -> TargetRun:
        reports = await run_suite()
        return TargetRun(
            model=model,
            turns=tuple(extract_turns(reports)),
            suite=score(reports),
        )

    return run
