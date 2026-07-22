"""Service-Level Objectives and error-budget accounting.

An :class:`SLO` names an objective (e.g. 0.999) over a window. Feeding it good
and total event counts yields an :class:`SLOReport`: success ratio, whether the
objective is met, remaining error budget (absolute events + percentage), and a
burn rate. Fail-closed: zero traffic never reports ``healthy`` — it reports
``unknown``.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SLOStatus(str, Enum):
    healthy = "healthy"
    breaching = "breaching"
    unknown = "unknown"  # undefined / zero-traffic — never assumed healthy


class SLOReport(BaseModel):
    """The computed state of an SLO for a given (good, total) sample."""

    model_config = ConfigDict(extra="forbid")

    name: str
    objective: float
    window: str
    good: int
    total: int
    status: SLOStatus
    # Ratios and budget are None when there is no traffic to reason about.
    success_ratio: float | None = None
    met: bool = False
    # Total error budget expressed as an allowed *count* of failures.
    error_budget_allowed: float | None = None
    # Remaining budget, absolute (allowed failures - observed failures) and %.
    error_budget_remaining: float | None = None
    error_budget_remaining_pct: float | None = None
    # Burn rate: observed failure ratio / allowed failure ratio.
    # 1.0 means budget is being spent exactly at the sustainable pace.
    burn_rate: float | None = None


class SLO(BaseModel):
    """A named objective over a descriptive window."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    objective: float  # target success ratio, strictly between 0 and 1
    window: str = ""  # descriptive label, e.g. "30d"

    @field_validator("objective")
    @classmethod
    def _objective_in_open_unit_interval(cls, v: float) -> float:
        if not (0.0 < v < 1.0):
            raise ValueError("objective must be strictly between 0 and 1")
        return v

    def evaluate(self, *, good: int, total: int) -> SLOReport:
        """Compute the report for a (good, total) sample. Fail-closed."""
        if good < 0 or total < 0:
            raise ValueError("good and total must be non-negative")
        if good > total:
            raise ValueError("good cannot exceed total")

        # No traffic: nothing is known. Never report healthy.
        if total == 0:
            return SLOReport(
                name=self.name,
                objective=self.objective,
                window=self.window,
                good=good,
                total=total,
                status=SLOStatus.unknown,
            )

        success_ratio = good / total
        met = success_ratio >= self.objective

        allowed_failure_ratio = 1.0 - self.objective
        observed_failure_ratio = 1.0 - success_ratio
        observed_failures = total - good

        allowed_failures = allowed_failure_ratio * total
        remaining = allowed_failures - observed_failures
        # Percentage of the budget still available (may be negative when spent
        # past exhaustion). allowed_failure_ratio > 0 because objective < 1.
        remaining_pct = (remaining / allowed_failures) * 100.0
        burn_rate = observed_failure_ratio / allowed_failure_ratio

        return SLOReport(
            name=self.name,
            objective=self.objective,
            window=self.window,
            good=good,
            total=total,
            status=SLOStatus.healthy if met else SLOStatus.breaching,
            success_ratio=success_ratio,
            met=met,
            error_budget_allowed=allowed_failures,
            error_budget_remaining=remaining,
            error_budget_remaining_pct=remaining_pct,
            burn_rate=burn_rate,
        )
