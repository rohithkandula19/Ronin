"""Structured engineering handoff artifacts for the role pipeline.

Each pipeline stage hands the next a *typed* artifact instead of only prose:
the architect emits an ``ArchitectPlan``, the implementer an
``ImplementationReport``, the reviewer a ``ReviewReport``, and tester/verifier a
``VerificationReport``. All are pure, serializable pydantic models.

Honesty rules baked in:
- Every field defaults to an EXPLICIT unknown (empty list / ``"unknown"`` /
  ``None``). A stage never fabricates a value it doesn't have.
- ``from_text`` leniently extracts a fenced ```json block from an agent reply
  and validates it; if none/invalid, it returns an all-unknown artifact with
  ``parsed=False`` (so callers can tell real structure from a fallback).
- ``finalize_verdict`` never turns *unknown* into *passed*.
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field

UNKNOWN = "unknown"
VERDICTS = ("passed", "failed", "blocked", "unknown")
CRITERION_STATUSES = ("met", "unmet", "unknown", "not_applicable")
SEVERITIES = ("info", "low", "medium", "high", "blocking")

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_block(text: str | None) -> dict | None:
    """Pull a JSON object out of an agent reply.

    Prefers the LAST fenced ```json block; falls back to the widest ``{...}``
    span. Returns None if nothing parses (so the caller uses an all-unknown
    artifact rather than inventing fields)."""
    if not text:
        return None
    candidates = _FENCE_RE.findall(text)
    if not candidates:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            candidates = [text[start:end + 1]]
    for blob in reversed(candidates):
        try:
            data = json.loads(blob)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _first_para(text: str | None, limit: int = 240) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    para = text.split("\n\n", 1)[0].replace("\n", " ").strip()
    return para if len(para) <= limit else para[: limit - 1] + "…"


class _Artifact(BaseModel):
    """Base: a human ``summary`` always present, ``parsed`` flags real structure."""
    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    parsed: bool = False

    @classmethod
    def from_text(cls, text: str | None, *, summary: str = ""):
        """Build an artifact from an agent reply (lenient). Never raises."""
        data = extract_json_block(text) or {}
        try:
            obj = cls.model_validate(data)
        except Exception:  # noqa: BLE001 — bad shape → all-unknown fallback
            obj = cls()
        obj.parsed = bool(data)
        obj.summary = summary or obj.summary or _first_para(text)
        return obj


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    text: str = ""
    status: str = UNKNOWN  # met / unmet / unknown / not_applicable


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = ""
    severity: str = "info"  # info / low / medium / high / blocking
    affected_files: list[str] = Field(default_factory=list)


class ArchitectPlan(_Artifact):
    kind: str = "architect_plan"
    task: str = ""
    objective: str = ""
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    files_to_inspect: list[str] = Field(default_factory=list)
    files_to_change: list[str] = Field(default_factory=list)
    implementation_steps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    test_strategy: str = ""
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)


class ImplementationReport(_Artifact):
    kind: str = "implementation_report"
    completed_steps: list[str] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    diff_summary: str = ""
    commands_requested: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)
    confidence: str = UNKNOWN  # low / medium / high / unknown


class ReviewReport(_Artifact):
    kind: str = "review_report"
    findings: list[ReviewFinding] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    safety_concerns: list[str] = Field(default_factory=list)
    architecture_concerns: list[str] = Field(default_factory=list)
    required_fixes: list[str] = Field(default_factory=list)
    approval_recommendation: str = UNKNOWN  # approve / request_changes / unknown


class VerificationReport(_Artifact):
    kind: str = "verification_report"
    tests_discovered: list[str] = Field(default_factory=list)
    tests_run: list[str] = Field(default_factory=list)
    passed: int | None = None
    failed: int | None = None
    blocked_reason: str = ""
    command_outputs_summary: str = ""
    acceptance_criteria_status: list[AcceptanceCriterion] = Field(default_factory=list)
    final_verdict: str = UNKNOWN  # passed / failed / blocked / unknown


# role key -> artifact class (single mapping the pipeline reads from)
ARTIFACT_BY_ROLE: dict[str, type[_Artifact]] = {
    "architect": ArchitectPlan,
    "implementer": ImplementationReport,
    "debugger": ImplementationReport,
    "reviewer": ReviewReport,
    "tester": VerificationReport,
    "verifier": VerificationReport,
}


def artifact_for_role(role: str) -> type[_Artifact]:
    """The artifact class a role produces (generic ``_Artifact`` for the rest)."""
    return ARTIFACT_BY_ROLE.get(role, _Artifact)


def finalize_verdict(report: VerificationReport) -> str:
    """Deterministic, conservative final verdict — **never upgrades unknown to
    passed**.

    - any acceptance criterion ``unmet`` or any failed test  -> ``failed``
    - a ``blocked_reason``                                    -> ``blocked``
    - any criterion ``unknown``                              -> ``unknown``
    - ``passed`` only if the agent said passed AND there's evidence (tests
      actually ran, or every criterion is met/not-applicable)
    - otherwise                                              -> ``unknown``
    """
    crits = report.acceptance_criteria_status
    if any(c.status == "unmet" for c in crits):
        return "failed"
    if (report.failed or 0) > 0:
        return "failed"
    if report.blocked_reason.strip():
        return "blocked"
    if any(c.status == "unknown" for c in crits):
        return "unknown"
    evidence = bool(report.tests_run) or (
        bool(crits) and all(c.status in ("met", "not_applicable") for c in crits))
    if report.final_verdict == "passed" and evidence:
        return "passed"
    if report.final_verdict in ("failed", "blocked"):
        return report.final_verdict
    return "unknown"


def roll_up_criteria(report: VerificationReport | None) -> dict[str, list[str]]:
    """Group a verifier's acceptance criteria into met / unmet / unknown /
    not_applicable buckets (by their text). Empty buckets when no report."""
    out: dict[str, list[str]] = {k: [] for k in CRITERION_STATUSES}
    if report is not None:
        for c in report.acceptance_criteria_status:
            out.setdefault(c.status if c.status in out else UNKNOWN, []).append(c.text or c.id)
    return out
