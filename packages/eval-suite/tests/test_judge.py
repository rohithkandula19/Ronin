from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ronin_eval_suite import EvalCase, Rubric, judge_one


def _resp(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )


def test_judge_parses_valid_response() -> None:
    client = MagicMock()
    client.messages.create.return_value = _resp(
        '<judgment>{"scores": {"task_success": 5, "faithfulness": 4}, "reasoning": "looks good"}</judgment>'
    )
    rubric = Rubric(criteria=["task_success", "faithfulness"])
    score = judge_one(EvalCase(id="x", input="?"), "answer", rubric, "claude-opus", client)

    assert score.scores == {"task_success": 5, "faithfulness": 4}
    assert "looks good" in score.reasoning
    assert score.error is None


def test_judge_handles_missing_tags() -> None:
    """When the judge skips the wrapper, we still try to parse the body."""
    client = MagicMock()
    client.messages.create.return_value = _resp(
        '{"scores": {"task_success": 3}, "reasoning": "ok"}'
    )
    rubric = Rubric(criteria=["task_success"])
    score = judge_one(EvalCase(id="x", input="?"), "answer", rubric, "judge", client)
    assert score.scores == {"task_success": 3}


def test_judge_handles_garbage_response() -> None:
    client = MagicMock()
    client.messages.create.return_value = _resp("complete nonsense, no JSON")
    rubric = Rubric(criteria=["task_success"])
    score = judge_one(EvalCase(id="x", input="?"), "answer", rubric, "judge", client)

    assert score.error is not None
    assert score.scores == {}
