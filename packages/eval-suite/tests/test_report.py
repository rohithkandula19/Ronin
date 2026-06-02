from __future__ import annotations

from pathlib import Path

from ronin_eval_suite import EvalScore, RunReport, Rubric, render_html_report


def test_html_report_renders_and_writes(tmp_path: Path) -> None:
    rubric = Rubric(criteria=["task_success", "faithfulness"])
    report = RunReport(
        target_model="claude-sonnet-4-6",
        judge_model="claude-opus-4-7",
        rubric=rubric,
        cases=[
            EvalScore(case_id="a", scores={"task_success": 5, "faithfulness": 4}, output="ok"),
            EvalScore(case_id="b", scores={"task_success": 3, "faithfulness": 5}, output="meh"),
            EvalScore(case_id="c", scores={}, output="", error="target failed"),
        ],
        label="run-1",
    )
    report.compute_summary()

    out = tmp_path / "report.html"
    html = render_html_report(report, out)

    assert out.exists()
    assert "<html>" in html
    assert "task_success" in html
    assert "claude-sonnet-4-6" in html
    assert "run-1" in html
    assert "(1 errored)" in html
