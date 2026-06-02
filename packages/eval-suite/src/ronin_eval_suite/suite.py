from __future__ import annotations

from typing import Callable

import anthropic
from pydantic import BaseModel, ConfigDict, Field

from .dataset import GoldenDataset
from .judge import judge_one
from .types import EvalCase, EvalScore, RunReport, Rubric

DEFAULT_TARGET_MODEL = "claude-sonnet-4-6"
DEFAULT_JUDGE_MODEL = "claude-opus-4-7"


def _default_target_runner(client: anthropic.Anthropic, model: str) -> Callable[[EvalCase], str]:
    """Vanilla single-turn target: feed ``case.input`` to the model, return text."""

    def run(case: EvalCase) -> str:
        response = client.messages.create(
            model=model,
            messages=[{"role": "user", "content": case.input}],
            max_tokens=2048,
        )
        return "\n".join(
            getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
        ).strip()

    return run


class EvalSuite(BaseModel):
    """Runs a target system over a golden dataset and judges each output.

    Provide a custom ``target_runner`` to evaluate an agent rather than a raw model:

        suite = EvalSuite(rubric=..., target_runner=lambda case: my_agent.run(case.input).output)

    The default runner sends ``case.input`` to ``target_model`` as a single user message.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    rubric: Rubric
    target_model: str = DEFAULT_TARGET_MODEL
    judge_model: str = DEFAULT_JUDGE_MODEL
    target_runner: Callable[[EvalCase], str] | None = None
    api_key: str | None = None
    label: str | None = None

    def run(self, dataset: GoldenDataset) -> RunReport:
        client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()
        runner = self.target_runner or _default_target_runner(client, self.target_model)

        scores: list[EvalScore] = []
        for case in dataset:
            try:
                output = runner(case)
            except Exception as exc:  # noqa: BLE001 — single-case failure should not kill the run
                scores.append(EvalScore(case_id=case.id, scores={}, output="", error=f"target failed: {exc}"))
                continue
            scores.append(judge_one(case, output, self.rubric, self.judge_model, client))

        report = RunReport(
            target_model=self.target_model,
            judge_model=self.judge_model,
            rubric=self.rubric,
            cases=scores,
            label=self.label,
        )
        report.compute_summary()
        return report
