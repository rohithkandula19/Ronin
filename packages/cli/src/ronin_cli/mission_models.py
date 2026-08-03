"""Schema-first state machine and handoffs for issue-to-PR missions.

Mission agents exchange these Pydantic models instead of raw conversation
history. The models are intentionally useful without a provider: they define
the durable contract for issue inspection, planning, implementation evidence,
testing, review, security, and approval before an executor is attached.
"""
from __future__ import annotations

import datetime as _datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def now_utc() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


class MissionStage(str, Enum):
    PENDING = "pending"
    INSPECTING = "inspecting"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    REVIEWING = "reviewing"
    SECURITY = "security"
    AWAITING_APPROVAL = "awaiting_approval"
    STAGING = "staging"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STAGES = frozenset({
    MissionStage.COMPLETED,
    MissionStage.FAILED,
    MissionStage.CANCELLED,
})

_TRANSITIONS: dict[MissionStage, frozenset[MissionStage]] = {
    MissionStage.PENDING: frozenset({MissionStage.INSPECTING, MissionStage.CANCELLED}),
    MissionStage.INSPECTING: frozenset({
        MissionStage.AWAITING_CLARIFICATION, MissionStage.PLANNING,
        MissionStage.FAILED, MissionStage.CANCELLED,
    }),
    MissionStage.AWAITING_CLARIFICATION: frozenset({
        MissionStage.PLANNING, MissionStage.CANCELLED,
    }),
    MissionStage.PLANNING: frozenset({
        MissionStage.IMPLEMENTING, MissionStage.AWAITING_APPROVAL,
        MissionStage.FAILED, MissionStage.CANCELLED,
    }),
    MissionStage.IMPLEMENTING: frozenset({
        MissionStage.TESTING, MissionStage.FAILED, MissionStage.CANCELLED,
    }),
    MissionStage.TESTING: frozenset({
        MissionStage.IMPLEMENTING, MissionStage.REVIEWING,
        MissionStage.FAILED, MissionStage.CANCELLED,
    }),
    MissionStage.REVIEWING: frozenset({
        MissionStage.IMPLEMENTING, MissionStage.SECURITY,
        MissionStage.AWAITING_APPROVAL, MissionStage.FAILED, MissionStage.CANCELLED,
    }),
    MissionStage.SECURITY: frozenset({
        MissionStage.IMPLEMENTING, MissionStage.AWAITING_APPROVAL,
        MissionStage.FAILED, MissionStage.CANCELLED,
    }),
    MissionStage.AWAITING_APPROVAL: frozenset({
        MissionStage.STAGING, MissionStage.CANCELLED,
    }),
    MissionStage.STAGING: frozenset({
        MissionStage.COMPLETED, MissionStage.FAILED, MissionStage.CANCELLED,
    }),
    MissionStage.COMPLETED: frozenset(),
    MissionStage.FAILED: frozenset({MissionStage.PLANNING, MissionStage.CANCELLED}),
    MissionStage.CANCELLED: frozenset(),
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MissionBudget(StrictModel):
    """Hard ceilings owned by a mission, not an individual provider call."""

    max_tokens: int = Field(default=100_000, ge=1, le=10_000_000)
    max_cost_usd: float = Field(default=25.0, ge=0.0, le=1_000_000.0)
    max_wall_time_seconds: int = Field(default=1_800, ge=1, le=604_800)
    max_tool_calls: int = Field(default=500, ge=1, le=100_000)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    max_repair_attempts: int = Field(default=3, ge=0, le=10)


class MissionUsage(StrictModel):
    tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)
    tool_calls: int = Field(default=0, ge=0)
    repair_attempts: int = Field(default=0, ge=0)

    def exceeded(self, budget: MissionBudget) -> tuple[str, ...]:
        exceeded: list[str] = []
        if self.tokens > budget.max_tokens:
            exceeded.append("tokens")
        if self.cost_usd > budget.max_cost_usd:
            exceeded.append("cost_usd")
        if self.wall_time_seconds > budget.max_wall_time_seconds:
            exceeded.append("wall_time_seconds")
        if self.tool_calls > budget.max_tool_calls:
            exceeded.append("tool_calls")
        if self.repair_attempts > budget.max_repair_attempts:
            exceeded.append("repair_attempts")
        return tuple(exceeded)


class MissionSpec(StrictModel):
    """Structured result of issue inspection and repository triage."""

    source: Literal["manual", "github", "gitlab"] = "manual"
    source_id: str = Field(default="", max_length=200)
    title: str = Field(min_length=1, max_length=500)
    issue_text: str = Field(min_length=1, max_length=50_000)
    repository_ref: str = Field(default="", max_length=500)
    requirements: list[str] = Field(default_factory=list, max_length=100)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=100)
    ambiguities: list[str] = Field(default_factory=list, max_length=50)
    risk_tags: list[str] = Field(default_factory=list, max_length=50)


class ContextSource(StrictModel):
    kind: Literal["file", "commit", "pull_request", "issue", "memory", "documentation"]
    reference: str = Field(min_length=1, max_length=1_000)
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)


class ContextPack(StrictModel):
    """The minimal, attributable context assembled for one role."""

    role: str = Field(min_length=1, max_length=100)
    summary: str = Field(default="", max_length=8_000)
    sources: list[ContextSource] = Field(default_factory=list, max_length=100)
    generated_at: str = Field(default_factory=now_utc)


class PlanStep(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2_000)
    files: list[str] = Field(default_factory=list, max_length=100)
    depends_on: list[str] = Field(default_factory=list, max_length=50)


class PlanArtifact(StrictModel):
    steps: list[PlanStep] = Field(default_factory=list, max_length=100)
    files_to_change: list[str] = Field(default_factory=list, max_length=500)
    test_additions: list[str] = Field(default_factory=list, max_length=100)
    dependency_impacts: list[str] = Field(default_factory=list, max_length=100)
    rollback_strategy: str = Field(default="", max_length=4_000)


class TestSuiteResult(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    command: str = Field(default="", max_length=2_000)
    status: Literal["passed", "failed", "blocked", "skipped", "unknown"] = "unknown"
    exit_code: int | None = None
    duration_seconds: float = Field(default=0.0, ge=0.0)
    output_summary: str = Field(default="", max_length=4_000)


class TestReport(StrictModel):
    suites: list[TestSuiteResult] = Field(default_factory=list, max_length=100)
    generated_tests: list[str] = Field(default_factory=list, max_length=100)
    coverage_delta: float | None = Field(default=None, ge=-100.0, le=100.0)
    repair_attempt: int = Field(default=0, ge=0, le=10)
    verdict: Literal["passed", "failed", "blocked", "unknown"] = "unknown"


class ReviewFinding(StrictModel):
    severity: Literal["info", "low", "medium", "high", "blocking"] = "info"
    title: str = Field(min_length=1, max_length=500)
    detail: str = Field(default="", max_length=4_000)
    file: str = Field(default="", max_length=1_000)
    line: int | None = Field(default=None, ge=1)
    suggested_patch: str = Field(default="", max_length=20_000)


class ReviewReport(StrictModel):
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=200)
    verdict: Literal["approve", "request_changes", "blocked", "unknown"] = "unknown"


class SecurityFinding(StrictModel):
    rule_id: str = Field(min_length=1, max_length=200)
    severity: Literal["low", "medium", "high", "blocking"] = "medium"
    detail: str = Field(min_length=1, max_length=4_000)
    file: str = Field(default="", max_length=1_000)
    line: int | None = Field(default=None, ge=1)


class SecurityScan(StrictModel):
    findings: list[SecurityFinding] = Field(default_factory=list, max_length=200)
    policy_version: str = Field(default="", max_length=200)
    verdict: Literal["passed", "failed", "blocked", "unknown"] = "unknown"


class MissionArtifacts(StrictModel):
    context_packs: list[ContextPack] = Field(default_factory=list, max_length=20)
    plan: PlanArtifact | None = None
    test_report: TestReport | None = None
    review_report: ReviewReport | None = None
    security_scan: SecurityScan | None = None


class MissionEvent(StrictModel):
    """Hash-chained audit event persisted separately from mutable mission state."""

    sequence: int = Field(ge=1)
    at: str = Field(default_factory=now_utc)
    actor: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    from_stage: MissionStage | None = None
    to_stage: MissionStage | None = None
    summary: str = Field(default="", max_length=4_000)
    artifact_kind: str = Field(default="", max_length=100)
    payload_digest: str = Field(default="", max_length=128)
    previous_hash: str = Field(default="", max_length=128)
    event_hash: str = Field(default="", min_length=64, max_length=128)


class MissionRecord(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    created: str = Field(default_factory=now_utc)
    updated: str = Field(default_factory=now_utc)
    stage: MissionStage = MissionStage.PENDING
    spec: MissionSpec
    budget: MissionBudget = Field(default_factory=MissionBudget)
    usage: MissionUsage = Field(default_factory=MissionUsage)
    artifacts: MissionArtifacts = Field(default_factory=MissionArtifacts)
    candidate_workspace_id: str | None = None
    event_count: int = Field(default=0, ge=0)
    last_event_hash: str = Field(default="", max_length=128)
    error: str | None = Field(default=None, max_length=4_000)


def can_transition(current: MissionStage, target: MissionStage) -> bool:
    return target in _TRANSITIONS[current]


def transition_record(record: MissionRecord, target: MissionStage) -> MissionRecord:
    """Return the next valid mission state without emitting an event or doing I/O."""
    if not can_transition(record.stage, target):
        raise ValueError(f"mission cannot transition from {record.stage.value} to {target.value}")
    return record.model_copy(update={"stage": target, "updated": now_utc(), "error": None})
