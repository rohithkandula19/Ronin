from __future__ import annotations

from pydantic import BaseModel, Field

from .types import RunReport


class DriftReport(BaseModel):
    """Pairwise comparison of two ``RunReport`` summaries."""

    baseline_label: str | None
    candidate_label: str | None
    deltas: dict[str, float] = Field(default_factory=dict)
    regressions: list[str] = Field(default_factory=list)
    threshold: float = 0.5

    @property
    def has_regression(self) -> bool:
        return bool(self.regressions)


def detect_drift(baseline: RunReport, candidate: RunReport, threshold: float = 0.5) -> DriftReport:
    """Compare two runs criterion-by-criterion. Flag criteria where the candidate dropped
    by more than ``threshold`` (in score units, before normalization)."""
    deltas: dict[str, float] = {}
    regressions: list[str] = []
    for criterion in baseline.rubric.criteria:
        b = baseline.summary.get(criterion, 0.0)
        c = candidate.summary.get(criterion, 0.0)
        delta = round(c - b, 3)
        deltas[criterion] = delta
        if delta < -abs(threshold):
            regressions.append(criterion)
    return DriftReport(
        baseline_label=baseline.label,
        candidate_label=candidate.label,
        deltas=deltas,
        regressions=regressions,
        threshold=threshold,
    )
