from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ronin_eval_suite import EvalCase, EvalSuite, GoldenDataset, Rubric


def _resp(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )


def test_suite_end_to_end_with_custom_runner() -> None:
    """Custom runner avoids hitting the target API; only the judge is mocked."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _resp(
        '<judgment>{"scores": {"task_success": 4, "helpfulness": 5}, "reasoning": "good"}</judgment>'
    )

    cases = [EvalCase(id="c1", input="hi"), EvalCase(id="c2", input="bye")]
    dataset = GoldenDataset(cases)

    with patch("ronin_eval_suite.suite.anthropic.Anthropic", return_value=fake_client):
        suite = EvalSuite(
            rubric=Rubric(criteria=["task_success", "helpfulness"]),
            target_runner=lambda case: f"answered: {case.input}",
            label="run-1",
        )
        report = suite.run(dataset)

    assert len(report.cases) == 2
    assert report.summary["task_success"] == 4.0
    assert report.summary["helpfulness"] == 5.0
    assert report.label == "run-1"
    # Two cases × one judge call each (no target call because of custom runner)
    assert fake_client.messages.create.call_count == 2


def test_target_failure_does_not_kill_run() -> None:
    """A single case crash should record an error, not abort the suite."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _resp(
        '<judgment>{"scores": {"task_success": 5}, "reasoning": "fine"}</judgment>'
    )
    cases = [EvalCase(id="bad", input="x"), EvalCase(id="good", input="y")]

    def runner(case: EvalCase) -> str:
        if case.id == "bad":
            raise RuntimeError("boom")
        return "ok"

    with patch("ronin_eval_suite.suite.anthropic.Anthropic", return_value=fake_client):
        suite = EvalSuite(
            rubric=Rubric(criteria=["task_success"]),
            target_runner=runner,
        )
        report = suite.run(GoldenDataset(cases))

    bad = next(c for c in report.cases if c.case_id == "bad")
    good = next(c for c in report.cases if c.case_id == "good")
    assert bad.error and "boom" in bad.error
    assert good.error is None
    assert report.summary["task_success"] == 5.0  # only the non-errored case counts
