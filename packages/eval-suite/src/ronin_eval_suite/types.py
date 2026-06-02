from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Rubric(BaseModel):
    """Scoring rubric for the judge.

    ``criteria`` is the list of dimensions to score (e.g. ``["task_success", "faithfulness"]``).
    ``scale`` is the inclusive integer range the judge scores on.
    """

    criteria: list[str]
    scale: tuple[int, int] = (1, 5)
    judge_instructions: str = ""


class EvalCase(BaseModel):
    """A single test case in the golden dataset."""

    id: str
    input: str
    expected: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalScore(BaseModel):
    """The judge's verdict for one case."""

    case_id: str
    scores: dict[str, int]
    reasoning: str = ""
    output: str = ""
    error: str | None = None


class RunReport(BaseModel):
    """Result of running a target model + judge over a dataset."""

    target_model: str
    judge_model: str
    rubric: Rubric
    cases: list[EvalScore]
    summary: dict[str, float] = Field(default_factory=dict)
    label: str | None = None

    def compute_summary(self) -> None:
        """Populate ``summary`` with mean per-criterion scores across non-errored cases."""
        for criterion in self.rubric.criteria:
            values = [c.scores.get(criterion) for c in self.cases if c.error is None]
            valid = [v for v in values if isinstance(v, int)]
            self.summary[criterion] = round(sum(valid) / len(valid), 3) if valid else 0.0
