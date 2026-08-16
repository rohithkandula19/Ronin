"""The training-stage ablation: five metrics per stage, base→+SFT→+DPO→+GRPO, one table.

Reuses the three-way ``TargetRun``/``SuiteScore`` and the metric functions, so these tests
pin the *new* arithmetic (median turns, cost/task), the assembly/ordering, the delta block,
and the honest ``—`` for anything unmeasured. The placeholder smoke run proves the table
structure exists before a single checkpoint does.
"""

from __future__ import annotations

import asyncio

from ronin_training.adapter.metrics import NOT_MEASURED, VALID_CALL, CallCheck, Setback, TurnRecord
from ronin_training.adapter.stage_ablation import (
    BASE,
    DPO,
    GRPO,
    SFT,
    AblationError,
    AblationTable,
    StageRun,
    assemble_ablation,
    placeholder_table,
    render_markdown,
    run_ablation,
    stage_metrics,
)
from ronin_training.adapter.threeway import SuiteScore, TargetRun


def _run(
    stage: str,
    *,
    valid: int,
    invalid: int,
    passed: int,
    cases: int,
    turns: tuple[int, ...],
    cost: float | None,
) -> StageRun:
    records = [TurnRecord(task_id="t", call=VALID_CALL, fingerprint=f"ok{i}") for i in range(valid)]
    records += [
        TurnRecord(task_id="t", call=CallCheck(ok=False, reason="schema"), fingerprint=f"bad{i}")
        for i in range(invalid)
    ]
    return StageRun(
        stage=stage,
        run=TargetRun(model=stage, turns=tuple(records), suite=SuiteScore(cases, passed)),
        task_turns=turns,
        cost_usd=cost,
    )


# --------------------------------------------------------------------------- #
# the five metrics
# --------------------------------------------------------------------------- #


def test_stage_metrics_computes_all_five() -> None:
    run = _run(SFT, valid=8, invalid=2, passed=9, cases=20, turns=(6, 8, 10), cost=0.30)
    m = stage_metrics(run)
    assert m.pass_at_1 == 9 / 20
    assert m.tool_validity == 8 / 10
    assert m.median_turns == 8  # median of (6, 8, 10)
    assert m.recovery is None  # no setbacks in this run → not measured, not 0
    assert m.cost_per_task == 0.30 / 3  # cost over the task-turn samples


def test_recovery_is_measured_when_setbacks_are_present() -> None:
    records = (
        TurnRecord(
            task_id="t", setback=Setback.FAILED_RESULT, setback_fingerprint="x"
        ),  # recovered
        TurnRecord(
            task_id="t",
            call=VALID_CALL,
            fingerprint="x",
            setback=Setback.FAILED_RESULT,
            setback_fingerprint="x",  # repeated → not recovered
        ),
    )
    run = StageRun(GRPO, TargetRun("g", records, SuiteScore(4, 2)), task_turns=(5, 6))
    assert stage_metrics(run).recovery == 0.5


def test_unmeasured_metrics_are_none_not_zero() -> None:
    run = StageRun(BASE, TargetRun("b", (), suite=None))  # no suite, no turns, no cost
    m = stage_metrics(run)
    assert m.pass_at_1 is None and m.tool_validity is None
    assert m.median_turns is None and m.recovery is None and m.cost_per_task is None


def test_cost_per_task_uses_suite_cases_when_no_turn_samples() -> None:
    run = StageRun(BASE, TargetRun("b", (), SuiteScore(10, 4)), cost_usd=1.0)  # no task_turns
    assert stage_metrics(run).cost_per_task == 0.1  # 1.0 / 10 cases


# --------------------------------------------------------------------------- #
# assembly + ordering + deltas
# --------------------------------------------------------------------------- #


def _ablation() -> AblationTable:
    runs = {
        BASE: _run(BASE, valid=4, invalid=6, passed=6, cases=20, turns=(9, 9, 9), cost=0.30),
        SFT: _run(SFT, valid=7, invalid=3, passed=9, cases=20, turns=(8, 8, 8), cost=0.28),
        GRPO: _run(GRPO, valid=9, invalid=1, passed=13, cases=20, turns=(6, 6, 6), cost=0.24),
    }
    return assemble_ablation(suite_id="s", suite_cases=20, runs=runs)


def test_assemble_orders_by_pipeline_and_skips_absent_stages() -> None:
    table = _ablation()
    assert [r.stage for r in table.rows] == [BASE, SFT, GRPO]  # DPO absent, in order, no gap


def test_unknown_stage_is_refused() -> None:
    try:
        assemble_ablation(
            suite_id="s",
            suite_cases=1,
            runs={"v9": _run(BASE, valid=1, invalid=0, passed=1, cases=1, turns=(1,), cost=0.0)},
        )
    except AblationError as exc:
        assert "unknown stage" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected AblationError")


def test_delta_vs_base_reads_each_metric_in_its_direction() -> None:
    table = _ablation()
    assert table.delta_vs_base(GRPO, "pass_at_1") == (13 / 20) - (6 / 20)  # more is better
    assert table.delta_vs_base(GRPO, "median_turns") == 6 - 9  # fewer is better (negative)
    assert table.delta_vs_base(BASE, "pass_at_1") == 0.0
    assert table.delta_vs_base(DPO, "pass_at_1") is None  # DPO absent


def test_empty_suite_id_is_refused() -> None:
    try:
        assemble_ablation(suite_id="", suite_cases=1, runs={})
    except AblationError as exc:
        assert "suite_id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected AblationError")


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def test_render_has_all_five_metric_columns_and_a_row_per_stage() -> None:
    md = render_markdown(_ablation())
    for header in (
        "pass@1",
        "tool-syntax validity",
        "median turns",
        "recovery rate",
        "cost / task",
    ):
        assert header in md
    assert "base Qwen2.5-Coder-1.5B" in md and "+SFT" in md and "+GRPO" in md
    assert "What each stage added" in md  # the delta block


def test_render_shows_em_dash_for_unmeasured_cells() -> None:
    runs = {BASE: StageRun(BASE, TargetRun("b", (), suite=None))}
    md = render_markdown(assemble_ablation(suite_id="s", suite_cases=0, runs=runs))
    assert NOT_MEASURED in md  # pass@1 / validity / etc. all unmeasured → em dash, not 0


def test_render_with_no_rows_says_so() -> None:
    md = render_markdown(assemble_ablation(suite_id="s", suite_cases=0, runs={}))
    assert "No stage produced a result" in md


# --------------------------------------------------------------------------- #
# run_ablation: sequential, failure-tolerant
# --------------------------------------------------------------------------- #


def test_run_ablation_survives_a_failing_stage() -> None:
    async def ok() -> StageRun:
        return _run(BASE, valid=5, invalid=5, passed=5, cases=20, turns=(9,), cost=0.3)

    async def boom() -> StageRun:
        raise RuntimeError("OOM on the box")

    table = asyncio.run(run_ablation(suite_id="s", suite_cases=20, runners={BASE: ok, SFT: boom}))
    base_row = table.row(BASE)
    sft_row = table.row(SFT)
    assert base_row is not None and base_row.pass_at_1 == 0.25
    assert sft_row is not None and sft_row.notes  # the failure is a note, not a crash
    assert "did not run" in sft_row.model


# --------------------------------------------------------------------------- #
# the smoke run — the ablation table structure before any checkpoint exists
# --------------------------------------------------------------------------- #


def test_placeholder_table_produces_the_full_structure() -> None:
    table = placeholder_table()
    assert [r.stage for r in table.rows] == [BASE, SFT, DPO, GRPO, "kimi"]
    md = render_markdown(table)
    # all five stages and all five metrics render, and it is flagged as placeholder
    for stage_label in ("base Qwen2.5-Coder-1.5B", "+SFT", "+DPO", "+GRPO", "Kimi (ceiling)"):
        assert stage_label in md
    assert "PLACEHOLDER" in md
    # the progression actually improves pass@1 in the placeholder
    passes = [r.pass_at_1 for r in table.rows if r.stage in (BASE, SFT, "dpo", GRPO)]
    assert passes == sorted(passes) and passes[0] is not None and passes[-1] > passes[0]
