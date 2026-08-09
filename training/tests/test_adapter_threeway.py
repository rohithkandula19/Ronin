"""The three-way report — with particular attention to the adapter *losing*.

If the losing outcome rendered badly, nobody would publish one, and the whole point of
this phase is honest measurement. So the losing path gets more assertions than the
winning one: it must have its own heading, name both deltas, and carry a concrete
iterate list.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from ronin_training.adapter.metrics import (
    NOT_MEASURED,
    VALID_CALL,
    CallCheck,
    Setback,
    TurnRecord,
)
from ronin_training.adapter.threeway import (
    ADAPTER,
    BASE,
    KIMI,
    TARGET_ORDER,
    Outcome,
    SuiteScore,
    TargetRun,
    ThreeWayError,
    ThreeWayReport,
    assemble_report,
    render_markdown,
    run_three_way,
    suite_score_of,
    target_runner,
    write_report,
)


def _turns(
    valid: int = 0, invalid: int = 0, adapted: int = 0, repeated: int = 0
) -> tuple[TurnRecord, ...]:
    """A target's transcript with a known validity and a known recovery rate."""
    out: list[TurnRecord] = []
    for index in range(valid):
        out.append(TurnRecord(task_id="t", call=VALID_CALL, fingerprint=f"ok:{index}"))
    for index in range(invalid):
        out.append(
            TurnRecord(
                task_id="t",
                call=CallCheck(ok=False, reason="call payload is not valid JSON: x"),
                fingerprint=f"bad:{index}",
            )
        )
    for index in range(adapted):
        out.append(
            TurnRecord(
                task_id="t",
                call=VALID_CALL,
                fingerprint=f"new:{index}",
                setback=Setback.FAILED_RESULT,
                setback_fingerprint=f"old:{index}",
            )
        )
    for index in range(repeated):
        out.append(
            TurnRecord(
                task_id="t",
                call=VALID_CALL,
                fingerprint=f"stuck:{index}",
                setback=Setback.DENIAL,
                setback_fingerprint=f"stuck:{index}",
            )
        )
    return tuple(out)


#: One target's shape: (valid, invalid, adapted-after-setback, repeated-after-setback).
Shape = tuple[int, int, int, int]


def _report(
    *,
    adapter: Shape,
    base: Shape,
    kimi: Shape | None = (10, 0, 4, 0),
) -> ThreeWayReport:
    runs = {
        ADAPTER: TargetRun(model="adapter-fake", turns=_turns(*adapter)),
        BASE: TargetRun(model="base-fake", turns=_turns(*base)),
    }
    if kimi is not None:
        runs[KIMI] = TargetRun(model="kimi-fake", turns=_turns(*kimi))
    return assemble_report(suite_id="phase11", suite_cases=12, runs=runs)


# ------------------------------------------------------------------ the verdict


def test_the_adapter_losing_on_both_metrics_is_a_first_class_outcome() -> None:
    report = _report(adapter=(6, 4, 1, 3), base=(9, 1, 3, 1))
    verdict = report.verdict()
    assert verdict.outcome is Outcome.ADAPTER_LOSES
    assert verdict.adapter_beat_base is False
    assert verdict.syntax_delta is not None and verdict.syntax_delta < 0
    assert verdict.recovery_delta is not None and verdict.recovery_delta < 0
    assert "did NOT beat base" in verdict.headline
    assert verdict.next_steps, "a losing verdict must say what to do next"


def test_a_tie_counts_as_not_beating_base() -> None:
    shape = (8, 2, 3, 1)
    report = _report(adapter=shape, base=shape)
    verdict = report.verdict()
    assert verdict.outcome is Outcome.ADAPTER_LOSES
    assert verdict.syntax_delta == pytest.approx(0.0)
    assert verdict.recovery_delta == pytest.approx(0.0)
    assert "A tie counts as not beating it" in verdict.headline


def test_winning_both_metrics_is_reported_as_a_win_with_both_deltas() -> None:
    report = _report(adapter=(10, 0, 4, 0), base=(6, 4, 1, 3))
    verdict = report.verdict()
    assert verdict.outcome is Outcome.ADAPTER_WINS
    assert verdict.adapter_beat_base is True
    assert verdict.syntax_delta is not None and verdict.syntax_delta > 0
    assert verdict.recovery_delta is not None and verdict.recovery_delta > 0
    assert verdict.next_steps == ()


@pytest.mark.parametrize(
    ("adapter", "base", "better", "worse"),
    [
        ((10, 0, 1, 3), (6, 4, 3, 1), "tool-syntax validity", "recovery rate"),
        ((6, 4, 4, 0), (9, 1, 1, 3), "recovery rate", "tool-syntax validity"),
    ],
)
def test_a_split_decision_is_reported_as_mixed_naming_which_side_lost(
    adapter: Shape,
    base: Shape,
    better: str,
    worse: str,
) -> None:
    verdict = _report(adapter=adapter, base=base).verdict()
    assert verdict.outcome is Outcome.MIXED
    assert f"beat base Qwen on {better}" in verdict.headline
    assert f"NOT on {worse}" in verdict.headline
    assert verdict.next_steps


def test_a_metric_with_no_measurements_refuses_to_declare_a_winner() -> None:
    """No recovery opportunity on either side means no recovery comparison."""
    report = _report(adapter=(10, 0, 0, 0), base=(2, 8, 0, 0))
    verdict = report.verdict()
    assert verdict.outcome is Outcome.NOT_RUN
    assert verdict.recovery_delta is None
    assert "recovery rate" in verdict.headline
    assert "no measurements" in verdict.headline


def test_a_missing_target_is_not_run_rather_than_a_default() -> None:
    report = assemble_report(
        suite_id="phase11",
        suite_cases=12,
        runs={ADAPTER: TargetRun(model="adapter-fake", turns=_turns(5, 0, 1, 0))},
    )
    verdict = report.verdict()
    assert verdict.outcome is Outcome.NOT_RUN
    assert "base" in verdict.headline


# ------------------------------------------------------------------- rendering


def test_the_losing_outcome_renders_legibly() -> None:
    report = _report(adapter=(6, 4, 1, 3), base=(9, 1, 3, 1))
    text = render_markdown(report)
    assert "## Outcome: the adapter did NOT beat base" in text
    assert "| metric | adapter minus base |" in text
    assert "### Iterate, in this order" in text
    assert "1. check the dialect first" in text
    assert "published as one" in text
    # the table is still there, with all three columns
    assert "| **tool-syntax validity** |" in text
    assert "| **recovery rate** |" in text
    assert "ronin-adapter-qwen1.5b" in text
    assert "base Qwen2.5-Coder-1.5B" in text
    assert "Kimi (reference ceiling)" in text
    # and the exact fractions, not just the rates: adapter attempts 6+4+1+3 = 14
    # turns, 6+1+3 = 10 of them valid; base attempts 14 with 13 valid.
    assert "10/14" in text
    assert "13/14" in text


def test_the_report_states_both_metric_definitions() -> None:
    text = render_markdown(_report(adapter=(6, 4, 1, 3), base=(9, 1, 3, 1)))
    assert "## What the two metrics mean, exactly" in text
    assert "**tool-syntax validity** —" in text
    assert "**recovery rate** —" in text
    assert "ronin.core.loop.fingerprint" in text
    assert "excluded from the denominator" in text


def test_an_unmeasured_cell_renders_as_a_dash_and_never_as_zero() -> None:
    report = _report(adapter=(10, 0, 0, 0), base=(8, 2, 0, 0), kimi=None)
    text = render_markdown(report)
    recovery_row = next(
        line for line in text.splitlines() if line.startswith("| **recovery rate**")
    )
    assert NOT_MEASURED in recovery_row
    assert "0.000" not in recovery_row
    assert "suite pass rate |" in text
    assert "means **not measured**" in text


def test_the_suite_pass_rate_appears_when_the_suite_reported_one() -> None:
    report = assemble_report(
        suite_id="phase11",
        suite_cases=12,
        runs={
            ADAPTER: TargetRun(
                model="a", turns=_turns(5, 5, 1, 1), suite=SuiteScore(cases=12, passed=4)
            ),
            BASE: TargetRun(
                model="b", turns=_turns(5, 5, 1, 1), suite=SuiteScore(cases=12, passed=6)
            ),
        },
    )
    text = render_markdown(report)
    assert "4/12" in text
    assert "6/12" in text


def test_the_per_target_detail_lists_the_invalid_call_reasons() -> None:
    text = render_markdown(_report(adapter=(6, 4, 1, 3), base=(9, 1, 3, 1)))
    assert "invalid-call reasons" in text
    assert "call payload is not valid JSON" in text
    assert "after approval_denial:" in text
    assert "after failed_tool_result:" in text


def test_kimi_is_labelled_a_ceiling_not_the_comparison() -> None:
    text = render_markdown(_report(adapter=(6, 4, 1, 3), base=(9, 1, 3, 1)))
    assert "reference ceiling" in text
    assert "against the base model it was trained from" in text


def test_a_report_with_no_results_says_so_instead_of_rendering_an_empty_table() -> None:
    text = render_markdown(assemble_report(suite_id="phase11", suite_cases=0, runs={}))
    assert "No target produced a result" in text
    assert "## Outcome: not run" in text


def test_write_report_writes_lf_endings(tmp_path: Path) -> None:
    out = write_report(_report(adapter=(6, 4, 1, 3), base=(9, 1, 3, 1)), tmp_path / "r.md")
    raw = out.read_bytes()
    assert b"\r\n" not in raw
    assert raw.startswith(b"# Three-way evaluation")


# ------------------------------------------------------------------- assembling


def test_assembling_requires_a_suite_id() -> None:
    with pytest.raises(ThreeWayError, match="suite_id is required"):
        assemble_report(suite_id="", suite_cases=1, runs={})


def test_an_unknown_target_name_is_rejected() -> None:
    with pytest.raises(ThreeWayError, match="unknown target"):
        assemble_report(
            suite_id="phase11",
            suite_cases=1,
            runs={"gpt": TargetRun(model="x", turns=())},
        )


def test_columns_come_out_in_the_declared_order_whatever_order_they_went_in() -> None:
    report = assemble_report(
        suite_id="phase11",
        suite_cases=1,
        runs={
            KIMI: TargetRun(model="k"),
            BASE: TargetRun(model="b"),
            ADAPTER: TargetRun(model="a"),
        },
    )
    assert [result.name for result in report.results] == list(TARGET_ORDER)


def test_a_target_run_must_name_its_model() -> None:
    with pytest.raises(ThreeWayError, match="must name the model"):
        TargetRun(model="")


def test_an_impossible_suite_score_is_rejected() -> None:
    with pytest.raises(ThreeWayError, match="impossible SuiteScore"):
        SuiteScore(cases=3, passed=4)


# ---------------------------------------------------- reading the suite's scoreboard


@dataclass
class _Scoreboard:
    cases: int
    passed: int


@dataclass
class _OtherScoreboard:
    total: int
    passed: int


@pytest.mark.parametrize(
    "board", [_Scoreboard(cases=9, passed=4), _OtherScoreboard(total=9, passed=4)]
)
def test_the_scoreboard_reducer_accepts_either_naming(board: object) -> None:
    score = suite_score_of(board)
    assert (score.cases, score.passed) == (9, 4)


def test_an_unreadable_scoreboard_fails_by_name_rather_than_attribute_error() -> None:
    with pytest.raises(ThreeWayError, match="cannot read a case count"):
        suite_score_of(object())


# ------------------------------------------------------------------ running it


async def test_running_three_targets_sequentially_produces_all_three_columns() -> None:
    order: list[str] = []

    def make(name: str) -> object:
        async def run() -> TargetRun:
            order.append(name)
            return TargetRun(model=f"{name}-fake", turns=_turns(5, 1, 2, 1))

        return run

    report = await run_three_way(
        suite_id="phase11",
        suite_cases=12,
        runners={KIMI: make("kimi"), ADAPTER: make("adapter"), BASE: make("base")},
        provenance=("fake run, no model",),
    )
    assert order == ["adapter", "base", "kimi"]
    assert [result.name for result in report.results] == list(TARGET_ORDER)
    assert report.provenance == ("fake run, no model",)


async def test_one_target_failing_does_not_destroy_the_others() -> None:
    """A Kimi outage must not throw away an adapter-vs-base comparison that ran."""

    async def ok() -> TargetRun:
        return TargetRun(model="ok-fake", turns=_turns(8, 2, 3, 1))

    async def boom() -> TargetRun:
        raise RuntimeError("no api key")

    report = await run_three_way(
        suite_id="phase11",
        suite_cases=12,
        runners={ADAPTER: ok, BASE: ok, KIMI: boom},
    )
    kimi = report.target(KIMI)
    assert kimi is not None
    assert "did not run" in kimi.model
    assert any("no api key" in note for note in kimi.notes)
    # the verdict still resolves from adapter vs base
    assert report.verdict().outcome is Outcome.ADAPTER_LOSES
    assert "note: target failed to run" in render_markdown(report)


async def test_target_runner_composes_the_suites_own_pieces() -> None:
    turns = _turns(4, 1, 1, 0)

    async def run_suite() -> object:
        return _Scoreboard(cases=5, passed=3)

    runner = target_runner(
        model="adapter-under-test",
        run_suite=run_suite,
        extract_turns=lambda _reports: turns,
    )
    run = await runner()
    assert run.model == "adapter-under-test"
    assert run.turns == turns
    assert run.suite == SuiteScore(cases=5, passed=3)
