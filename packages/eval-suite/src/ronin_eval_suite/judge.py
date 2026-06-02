from __future__ import annotations

import json
import re
from typing import Any

import anthropic

from .types import EvalCase, EvalScore, Rubric


def _judge_prompt(case: EvalCase, output: str, rubric: Rubric) -> str:
    expected = f"Expected (gold):\n{case.expected}\n" if case.expected else ""
    criteria_lines = "\n".join(f"- {c}" for c in rubric.criteria)
    lo, hi = rubric.scale
    return (
        f"You are an expert evaluator. Score the system's output against the rubric.\n\n"
        f"Task input:\n{case.input}\n\n"
        f"{expected}"
        f"System output:\n{output}\n\n"
        f"Rubric (score each {lo}-{hi}, integer):\n{criteria_lines}\n\n"
        f"{rubric.judge_instructions}\n\n"
        "Respond with JSON only, wrapped in <judgment></judgment> tags. "
        "Schema: {\"scores\": {<criterion>: <int>}, \"reasoning\": <string>}"
    )


def judge_one(
    case: EvalCase,
    output: str,
    rubric: Rubric,
    judge_model: str,
    client: anthropic.Anthropic,
) -> EvalScore:
    """Score one (case, output) pair using the judge model.

    Returns an ``EvalScore`` even on parse failure (with ``error`` set), so a single
    flaky judge response does not abort an entire run.
    """
    response = client.messages.create(
        model=judge_model,
        system="You are a strict, calibrated evaluator. Output JSON only.",
        messages=[{"role": "user", "content": _judge_prompt(case, output, rubric)}],
        max_tokens=1024,
    )
    text = "\n".join(
        getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
    ).strip()

    match = re.search(r"<judgment>(.*?)</judgment>", text, re.DOTALL)
    payload = match.group(1).strip() if match else text

    try:
        parsed: dict[str, Any] = json.loads(payload)
        raw_scores = parsed.get("scores", {})
        scores: dict[str, int] = {}
        for criterion in rubric.criteria:
            value = raw_scores.get(criterion)
            if isinstance(value, (int, float)):
                scores[criterion] = int(value)
        if not scores:
            raise ValueError("no rubric criteria scored")
        return EvalScore(
            case_id=case.id,
            scores=scores,
            reasoning=str(parsed.get("reasoning", "")),
            output=output,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        return EvalScore(
            case_id=case.id,
            scores={},
            output=output,
            error=f"judge parse failure: {exc}",
        )
