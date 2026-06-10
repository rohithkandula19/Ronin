from __future__ import annotations

from ronin_eval_suite import RunReport, Rubric, detect_drift


def _report(label: str, summary: dict[str, float]) -> RunReport:
    return RunReport(
        target_model="t",
        judge_model="j",
        rubric=Rubric(criteria=list(summary.keys())),
        cases=[],
        summary=summary,
        label=label,
    )


def test_drift_no_regression() -> None:
    base = _report("v1", {"task_success": 4.0, "safety": 5.0})
    cand = _report("v2", {"task_success": 4.2, "safety": 5.0})
    drift = detect_drift(base, cand, threshold=0.5)
    assert not drift.has_regression
    assert drift.deltas["task_success"] == 0.2


def test_drift_flags_regression() -> None:
    base = _report("v1", {"task_success": 4.5})
    cand = _report("v2", {"task_success": 3.5})
    drift = detect_drift(base, cand, threshold=0.5)
    assert drift.has_regression
    assert "task_success" in drift.regressions
