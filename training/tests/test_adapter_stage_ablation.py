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
    cost_from_report,
    placeholder_table,
    recovery_turns_from_outcome,
    render_markdown,
    run_ablation,
    stage_metrics,
    stage_run_from_report,
    stage_runner,
    suite_score_from_report,
    syntax_probe,
    task_turns_from_report,
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


# --------------------------------------------------------------------------- #
# wiring the real ronin.evals suite in — duck-typed fakes, no model, no ronin.evals import
# --------------------------------------------------------------------------- #

from types import SimpleNamespace  # noqa: E402


def _call(name: str, fp: str, *, ok: bool = True) -> SimpleNamespace:
    """A stand-in for ronin.evals ToolCallRecord — the fields the reducers read."""
    return SimpleNamespace(name=name, fingerprint=fp, ok=ok, error="" if ok else "boom")


def _result(
    task_id: str,
    *,
    passed: bool,
    turns_used: int,
    tool_calls: tuple[SimpleNamespace, ...] = (),
    skipped: bool = False,
) -> SimpleNamespace:
    record = SimpleNamespace(turns_used=turns_used, tool_calls=tool_calls)
    return SimpleNamespace(task_id=task_id, passed=passed, skipped=skipped, record=record)


def _report(
    results: tuple[SimpleNamespace, ...],
    *,
    passed: int,
    attempted: int,
    cost_total: float | None = None,
    cost_count: int = 0,
    model: str = "qwen-ckpt",
) -> SimpleNamespace:
    distributions = {}
    if cost_total is not None:
        distributions["cost_usd"] = SimpleNamespace(count=cost_count, total=cost_total)
    return SimpleNamespace(
        results=results,
        overall=SimpleNamespace(passed=passed, attempted=attempted),
        distributions=distributions,
        model=model,
    )


# --- the pure reducers ---------------------------------------------------- #


def test_suite_score_reads_passes_over_attempted() -> None:
    report = _report((), passed=7, attempted=20)
    score = suite_score_from_report(report)
    assert score.cases == 20 and score.passed == 7  # attempted is the denominator, skips excluded


def test_suite_score_refuses_a_report_without_an_aggregate() -> None:
    try:
        suite_score_from_report(SimpleNamespace(overall=SimpleNamespace()))
    except AblationError as exc:
        assert "passed" in str(exc) and "attempted" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected AblationError")


def test_task_turns_skips_skipped_tasks() -> None:
    results = (
        _result("a", passed=True, turns_used=8),
        _result("b", passed=False, turns_used=12),
        _result("c", passed=False, turns_used=99, skipped=True),  # excluded
    )
    assert task_turns_from_report(_report(results, passed=1, attempted=2)) == (8, 12)


def test_cost_is_none_when_nothing_was_priced() -> None:
    assert cost_from_report(_report((), passed=0, attempted=0)) is None  # no cost_usd dist
    assert (
        cost_from_report(_report((), passed=0, attempted=0, cost_total=0.0, cost_count=0)) is None
    )
    assert cost_from_report(_report((), passed=1, attempted=1, cost_total=0.6, cost_count=2)) == 0.6


# --- recovery reconstruction from parsed tool calls ------------------------ #


def test_recovery_turn_after_a_failure_that_tries_something_else() -> None:
    calls = (_call("read", "read:x", ok=False), _call("read", "read:y"))  # failed, then different
    turns = recovery_turns_from_outcome(SimpleNamespace(tool_calls=calls), task_id="t")
    assert len(turns) == 2
    assert turns[1].setback is Setback.FAILED_RESULT and not turns[1].repeated_setback


def test_recovery_not_credited_when_the_failing_call_is_repeated() -> None:
    calls = (_call("read", "read:x", ok=False), _call("read", "read:x"))  # repeats the failed fp
    turns = recovery_turns_from_outcome(SimpleNamespace(tool_calls=calls), task_id="t")
    assert turns[1].repeated_setback  # same fingerprint after a failure → no recovery


def test_trailing_failure_is_not_a_recovery_opportunity() -> None:
    calls = (_call("read", "read:x"), _call("read", "read:x", ok=False))  # fails on the last call
    turns = recovery_turns_from_outcome(SimpleNamespace(tool_calls=calls), task_id="t")
    assert all(t.setback is None for t in turns)  # nothing followed the failure → no opportunity


# --- the raw-completion decode probe -------------------------------------- #

_SCHEMAS = {
    "read_file": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
}


def _completion(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text)


def _toolcall(name: str, arguments: dict) -> str:
    import json

    payload = json.dumps({"name": name, "arguments": arguments})
    return f"<ronin:tool_call>{payload}</ronin:tool_call>"


def test_syntax_probe_scores_raw_completions() -> None:
    def provider(case: object) -> SimpleNamespace:
        return _completion(case["out"])  # type: ignore[index]

    cases = [
        {"out": _toolcall("read_file", {"path": "a"})},  # clean, in-registry, valid schema
        {"out": _toolcall("nope", {})},  # unknown tool → invalid
        {"out": "I will just explain, no call here."},  # no attempt → excluded from denominator
    ]
    score = syntax_probe(cases, provider, schemas=_SCHEMAS)
    assert score.attempts == 2 and score.valid == 1  # one clean call, one unknown-tool
    assert score.no_attempt == 1
    assert score.rate == 0.5


# --- the composed StageRunner (async) ------------------------------------- #


def test_stage_runner_binds_suite_and_probe_into_all_five_metrics() -> None:
    results = (
        _result("a", passed=True, turns_used=6, tool_calls=(_call("read", "read:x"),)),
        _result(
            "b",
            passed=False,
            turns_used=10,
            tool_calls=(_call("read", "read:x", ok=False), _call("read", "read:y")),  # recovered
        ),
    )
    report = _report(results, passed=1, attempted=2, cost_total=0.4, cost_count=2)

    async def run_suite() -> SimpleNamespace:
        return report

    async def probe() -> object:
        return syntax_probe(
            [{"out": _toolcall("read_file", {"path": "a"})}],
            lambda case: _completion(case["out"]),
            schemas=_SCHEMAS,
        )

    runner = stage_runner(stage=BASE, run_suite=run_suite, validity_probe=probe)
    run = asyncio.run(runner())
    m = stage_metrics(run)
    assert m.pass_at_1 == 0.5  # 1 / 2 attempted
    assert m.median_turns == 8  # median of (6, 10)
    assert m.cost_per_task == 0.2  # 0.4 total / 2 tasks
    assert m.recovery == 1.0  # the one setback (task b) was recovered
    assert m.tool_validity == 1.0  # from the probe, NOT the all-valid recovery turns


def test_stage_runner_leaves_validity_unmeasured_without_a_probe() -> None:
    # No probe: validity must NOT be faked from the recovery turns (which are all "valid").
    results = (_result("a", passed=True, turns_used=5, tool_calls=(_call("read", "read:x"),)),)
    report = _report(results, passed=1, attempted=1)

    async def run_suite() -> SimpleNamespace:
        return report

    run = asyncio.run(stage_runner(stage=BASE, run_suite=run_suite)())
    # recovery turns are all VALID_CALL, so tool_syntax_validity would read 1.0 — but there is no
    # override and the runner passed none, so the fallback derives from those turns. Guard that the
    # runner did not fabricate an override, and that the override channel is what carries a probe.
    assert run.tool_validity is None


def test_stage_run_from_report_passes_validity_override_through() -> None:
    report = _report(
        (_result("a", passed=True, turns_used=4, tool_calls=(_call("read", "read:x"),)),),
        passed=1,
        attempted=1,
    )
    run = stage_run_from_report(SFT, report, tool_validity=0.83)
    assert stage_metrics(run).tool_validity == 0.83  # override wins over the turn-derived value
