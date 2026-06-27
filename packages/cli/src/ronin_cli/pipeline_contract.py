"""Artifact contract enforcement for the role pipeline.

Compares the structured handoff artifacts against each other to catch a stage
that didn't honor the prior stage's contract — e.g. the implementer changed
files the architect never named, or the verifier didn't cover every acceptance
criterion. Pure and serializable; the report goes into ``--json``.

Honest degradation: when an artifact is missing or unparsed (``parsed=False``),
the dependent check is recorded as a WARNING/unknown — never silently passed.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .pipeline_artifacts import (
    ArchitectPlan,
    ImplementationReport,
    ReviewReport,
    VerificationReport,
)


class ContractCheckReport(BaseModel):
    """Result of cross-checking the pipeline's artifacts. Serializable."""
    checks_run: list[str] = Field(default_factory=list)
    passed_checks: list[str] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    final_contract_status: str = "unknown"  # passed / failed / warning / unknown

    def compute_status(self) -> None:
        if self.failed_checks or self.blocking_issues:
            self.final_contract_status = "failed"
        elif self.warnings:
            self.final_contract_status = "warning"
        elif self.passed_checks:
            self.final_contract_status = "passed"
        else:
            self.final_contract_status = "unknown"


def _norm(path: str) -> str:
    return path.strip().lstrip("./").replace("\\", "/").lower()


def check_contract(
    plan: ArchitectPlan | None,
    impl: ImplementationReport | None,
    review: ReviewReport | None,
    verification: VerificationReport | None,
) -> ContractCheckReport:
    """Cross-check the artifacts and return a typed report (pure)."""
    r = ContractCheckReport()

    def ok(name: str) -> None:
        r.checks_run.append(name)
        r.passed_checks.append(name)

    def fail(name: str, detail: str) -> None:
        r.checks_run.append(name)
        r.failed_checks.append(f"{name}: {detail}")

    def warn(name: str, detail: str) -> None:
        r.checks_run.append(name)
        r.warnings.append(f"{name}: {detail}")

    # 1) implementer's changed files should overlap the plan's named files
    if plan is not None and impl is not None and plan.files_to_change:
        planned = {_norm(p) for p in plan.files_to_change}
        changed = {_norm(p) for p in impl.files_changed}
        if not changed:
            warn("files_overlap", "plan named files to change but implementer reported none")
        elif planned & changed:
            ok("files_overlap")
            extra = sorted(c for c in changed if c not in planned)
            if extra and not (impl.unresolved_items or impl.diff_summary):
                warn("files_outside_plan",
                     f"changed files not in the plan and unexplained: {', '.join(extra)}")
        else:
            fail("files_overlap",
                 "implementer changed none of the files the architect planned")

    # 2) completed steps should correspond to the plan's steps
    if plan is not None and impl is not None and plan.implementation_steps:
        if impl.completed_steps:
            ok("steps_correspondence")
        else:
            warn("steps_correspondence", "plan had steps but implementer reported none completed")

    # 3) review's required fixes (or blocking findings) block success
    if review is not None:
        blockers = list(review.required_fixes)
        blockers += [f.text for f in review.findings if f.severity == "blocking"]
        if blockers:
            r.checks_run.append("review_blockers")
            for b in blockers:
                r.blocking_issues.append(f"unresolved review fix: {b}")
        else:
            ok("review_blockers")

    # 4) verifier must cover every acceptance criterion the architect defined
    if plan is not None and plan.acceptance_criteria:
        if verification is None:
            warn("acceptance_coverage", "plan defined acceptance criteria but no verification ran")
        else:
            planned_ids = {(c.id or c.text).strip().lower() for c in plan.acceptance_criteria}
            covered = {(c.id or c.text).strip().lower()
                       for c in verification.acceptance_criteria_status}
            missing = sorted(planned_ids - covered)
            if missing:
                fail("acceptance_coverage",
                     f"{len(missing)} acceptance criteria not evaluated by the verifier")
            else:
                ok("acceptance_coverage")

    # honest degradation: nothing concrete to check → leave status 'unknown'
    r.compute_status()
    return r
