"""Deterministic, evidence-gated Issue-to-PR workflow for durable missions.

This module deliberately owns no provider prompt or raw chat transcript. A
planner or implementation agent submits the typed artifacts from
``mission_models``; the workflow records them, runs candidate verification only
inside Docker, performs deterministic review/security checks, and refuses to
prepare a PR draft without passing evidence.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Callable

from .candidate_workspace import CandidateCommandResult, CandidateWorkspace, CandidateWorkspaceService
from .guard import changed_files, scan_added
from .mission_models import (
    EvaluationCheck,
    EvaluationGate,
    MissionArtifacts,
    MissionRecord,
    MissionStage,
    MissionUsage,
    PlanArtifact,
    PullRequestDraft,
    ReviewFinding,
    ReviewReport,
    SecurityFinding,
    SecurityScan,
    TestReport,
    TestSuiteResult,
)
from .mission_store import MissionStore
from .secret_scan import find_secrets


class MissionWorkflow:
    """Advance one mission only when its typed evidence supports the transition."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.store = MissionStore(self.root)
        self.candidates = CandidateWorkspaceService(self.root)

    def record_plan(self, mission_id: str, plan: PlanArtifact, *, actor: str = "architect") -> MissionRecord:
        """Store a structured plan and make the mission eligible for implementation."""
        mission = self.store.load(mission_id)
        if mission.stage is MissionStage.PENDING:
            mission = self.store.transition(
                mission.id, MissionStage.INSPECTING, actor=actor, summary="MissionSpec inspected",
            )
        if mission.stage not in {MissionStage.INSPECTING, MissionStage.AWAITING_CLARIFICATION}:
            raise ValueError(f"mission must be inspecting before a plan is recorded; it is {mission.stage.value}")
        mission = self.store.transition(
            mission.id, MissionStage.PLANNING, actor=actor, summary="structured implementation plan prepared",
        )
        mission = self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"plan": plan}),
        )
        return self.store.transition(
            mission.id, MissionStage.IMPLEMENTING, actor=actor, summary="plan approved for candidate implementation",
        )

    def verify_candidate(
        self,
        mission_id: str,
        command: str,
        *,
        timeout: int = 600,
        run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> MissionRecord:
        """Run one candidate test command and route only real evidence onward."""
        mission = self._require_stage(mission_id, MissionStage.IMPLEMENTING)
        candidate = self._candidate_for(mission)
        testing = self.store.transition(
            mission.id, MissionStage.TESTING, actor="test-gate", summary="candidate verification started",
        )
        if testing.usage.tool_calls >= testing.budget.max_tool_calls:
            return self._record_blocked_test(
                testing, command, "mission tool-call budget is exhausted before verification",
            )

        result = self.candidates.run_command(candidate.id, command, timeout=timeout, run_fn=run_fn)
        usage = self._usage_after_test(testing.usage, result)
        testing = self.store.set_usage(testing.id, usage, actor="test-gate")
        report = _test_report(command, result, repair_attempt=usage.repair_attempts)
        testing = self.store.save_artifacts(
            testing.id, testing.artifacts.model_copy(update={"test_report": report}),
        )
        if (
            not result.passed
            and not result.blocked
            and not result.timed_out
            and usage.repair_attempts > testing.budget.max_repair_attempts
        ):
            return self.store.transition(
                testing.id,
                MissionStage.FAILED,
                actor="test-gate",
                summary="candidate test retry budget exhausted",
                error=f"verification failed after {usage.repair_attempts} repair attempt(s); retry budget exhausted",
            )
        exceeded = usage.exceeded(testing.budget)
        if exceeded:
            return self.store.transition(
                testing.id,
                MissionStage.FAILED,
                actor="test-gate",
                summary="mission resource budget exceeded during verification",
                error="verification exceeded mission budget: " + ", ".join(exceeded),
            )
        if result.passed:
            return self.store.transition(
                testing.id, MissionStage.REVIEWING, actor="test-gate", summary="candidate verification passed",
            )
        if result.blocked or result.timed_out:
            return self.store.transition(
                testing.id,
                MissionStage.FAILED,
                actor="test-gate",
                summary="candidate verification was blocked",
                error=_failure_reason(result),
            )
        return self.store.transition(
            testing.id,
            MissionStage.IMPLEMENTING,
            actor="test-gate",
            summary=f"candidate test failed; repair attempt {usage.repair_attempts} is allowed",
        )

    def review_candidate(self, mission_id: str) -> MissionRecord:
        """Perform deterministic diff hygiene review before the security gate."""
        mission = self._require_stage(mission_id, MissionStage.REVIEWING)
        candidate = self._candidate_for(mission)
        diff = self.candidates.diff(candidate.id)
        report = _structural_review(Path(candidate.path), diff)
        reviewed = self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"review_report": report}),
        )
        if report.verdict == "approve":
            return self.store.transition(
                reviewed.id, MissionStage.SECURITY, actor="review-gate", summary="structural review approved",
            )
        return self.store.transition(
            reviewed.id,
            MissionStage.IMPLEMENTING,
            actor="review-gate",
            summary="structural review requested changes",
        )

    def scan_candidate_security(self, mission_id: str) -> MissionRecord:
        """Scan only candidate-added code and require a clean scan for approval."""
        mission = self._require_stage(mission_id, MissionStage.SECURITY)
        candidate = self._candidate_for(mission)
        scan = _security_scan(self.candidates.diff(candidate.id))
        scanned = self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"security_scan": scan}),
        )
        if scan.verdict == "passed":
            return self.store.transition(
                scanned.id,
                MissionStage.AWAITING_APPROVAL,
                actor="security-gate",
                summary="candidate security scan passed",
            )
        return self.store.transition(
            scanned.id,
            MissionStage.IMPLEMENTING,
            actor="security-gate",
            summary="candidate security scan found blocking evidence",
        )

    def evaluate(self, mission_id: str) -> MissionRecord:
        """Evaluate release readiness and retain the result as a typed artifact."""
        mission = self.store.load(mission_id)
        candidate, diff = self._candidate_and_diff(mission)
        checks = _evaluation_checks(mission, candidate, diff, self.store.verify_audit(mission.id).valid)
        gate = EvaluationGate(checks=checks, eligible=all(check.status == "passed" for check in checks))
        return self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"evaluation_gate": gate}),
        )

    def prepare_pull_request_draft(self, mission_id: str, *, approved_by: str) -> MissionRecord:
        """Create a local, reviewable PR draft after explicit human approval.

        The operation never creates a branch, commits, pushes, or calls GitHub.
        Those remain separate operator-approved publication actions.
        """
        if not approved_by.strip():
            raise ValueError("an approving human identity is required for a PR draft")
        mission = self._require_stage(mission_id, MissionStage.AWAITING_APPROVAL)
        evaluated = self.evaluate(mission.id)
        gate = evaluated.artifacts.evaluation_gate
        if gate is None or not gate.eligible:
            raise ValueError("mission evaluation gate is not eligible for a PR draft")
        candidate, diff = self._candidate_and_diff(evaluated)
        draft = _draft_pull_request(evaluated, candidate, diff, approved_by.strip())
        drafted = self.store.save_artifacts(
            evaluated.id, evaluated.artifacts.model_copy(update={"pull_request_draft": draft}),
        )
        return self.store.transition(
            drafted.id,
            MissionStage.STAGING,
            actor=f"approval:{approved_by.strip()}",
            summary="human-approved local pull-request draft prepared",
        )

    def _require_stage(self, mission_id: str, stage: MissionStage) -> MissionRecord:
        mission = self.store.load(mission_id)
        if mission.stage is not stage:
            raise ValueError(f"mission must be {stage.value}; it is {mission.stage.value}")
        return mission

    def _candidate_for(self, mission: MissionRecord) -> CandidateWorkspace:
        if not mission.candidate_workspace_id:
            raise ValueError("mission has no candidate workspace")
        candidate = self.candidates.load(mission.candidate_workspace_id)
        if candidate.mission_id != mission.id:
            raise ValueError("attached candidate workspace belongs to a different mission")
        if candidate.status != "active":
            raise ValueError(f"candidate workspace is {candidate.status}")
        return candidate

    def _candidate_and_diff(self, mission: MissionRecord) -> tuple[CandidateWorkspace | None, str]:
        try:
            candidate = self._candidate_for(mission)
            return candidate, self.candidates.diff(candidate.id)
        except ValueError:
            return None, ""

    def _usage_after_test(self, usage: MissionUsage, result: CandidateCommandResult) -> MissionUsage:
        return usage.model_copy(update={
            "tool_calls": usage.tool_calls + 1,
            "wall_time_seconds": usage.wall_time_seconds + result.duration_seconds,
            "repair_attempts": usage.repair_attempts + (0 if result.passed else 1),
        })

    def _record_blocked_test(self, mission: MissionRecord, command: str, reason: str) -> MissionRecord:
        report = TestReport(
            suites=[TestSuiteResult(name="candidate", command=command, status="blocked", output_summary=reason)],
            repair_attempt=mission.usage.repair_attempts,
            verdict="blocked",
        )
        blocked = self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"test_report": report}),
        )
        return self.store.transition(
            blocked.id, MissionStage.FAILED, actor="test-gate", summary="candidate verification blocked", error=reason,
        )


def _test_report(command: str, result: CandidateCommandResult, *, repair_attempt: int) -> TestReport:
    if result.passed:
        status, verdict, summary = "passed", "passed", "candidate command completed with exit code 0"
    elif result.blocked or result.timed_out:
        status, verdict, summary = "blocked", "blocked", _failure_reason(result)
    else:
        status, verdict, summary = "failed", "failed", _failure_reason(result)
    return TestReport(
        suites=[
            TestSuiteResult(
                name="candidate",
                command=command,
                status=status,
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
                output_summary=summary,
            )
        ],
        repair_attempt=repair_attempt,
        verdict=verdict,
    )


def _failure_reason(result: CandidateCommandResult) -> str:
    if result.timed_out:
        return result.error or "candidate command timed out"
    if result.blocked:
        return "candidate Docker command could not start"
    return f"candidate command exited with code {result.exit_code if result.exit_code is not None else 'unknown'}"


def _structural_review(root: Path, diff: str) -> ReviewReport:
    findings: list[ReviewFinding] = []
    if not diff.strip():
        findings.append(ReviewFinding(severity="blocking", title="Candidate diff is empty", detail="No implementation evidence exists."))
    for finding in scan_added(diff):
        findings.append(
            ReviewFinding(
                severity="blocking" if finding.severity == "high" else "low",
                title=f"{finding.kind} detected in candidate diff",
                detail="Remove or resolve this diff hygiene finding before staging.",
                file=finding.file,
            )
        )
    try:
        checked = subprocess.run(
            ["git", "-C", str(root), "diff", "--check", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        checked = None
    if checked is None or checked.returncode != 0:
        findings.append(
            ReviewFinding(
                severity="blocking",
                title="Git whitespace check did not pass",
                detail="Resolve whitespace errors or investigate the unavailable Git check.",
            )
        )
    blockers = [finding.title for finding in findings if finding.severity == "blocking"]
    return ReviewReport(findings=findings, verdict="request_changes" if blockers else "approve")


def _security_scan(diff: str) -> SecurityScan:
    findings: list[SecurityFinding] = []
    seen: set[tuple[str, str]] = set()
    from .guard import iter_added_lines

    for path, text in iter_added_lines(diff):
        for finding in find_secrets(text, path):
            key = (str(finding["path"]), str(finding["kind"]))
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                SecurityFinding(
                    rule_id=f"secret:{finding['kind']}",
                    severity="blocking",
                    detail=f"Potential {finding['kind']} added to the candidate ({finding['hint']}).",
                    file=str(finding["path"]),
                )
            )
    return SecurityScan(
        findings=findings,
        policy_version="mission-security-v1",
        verdict="failed" if findings else "passed",
    )


def _evaluation_checks(
    mission: MissionRecord,
    candidate: CandidateWorkspace | None,
    diff: str,
    audit_valid: bool,
) -> list[EvaluationCheck]:
    artifacts = mission.artifacts
    checks = [
        EvaluationCheck(
            name="plan",
            status="passed" if artifacts.plan and artifacts.plan.steps else "failed",
            detail="structured plan is recorded" if artifacts.plan and artifacts.plan.steps else "no structured plan steps are recorded",
        ),
        EvaluationCheck(
            name="candidate",
            status="passed" if candidate else "failed",
            detail="active candidate workspace is attached" if candidate else "no active candidate workspace is attached",
        ),
        EvaluationCheck(
            name="candidate_diff",
            status="passed" if diff.strip() else "failed",
            detail="candidate diff contains implementation evidence" if diff.strip() else "candidate diff is empty or unavailable",
        ),
        EvaluationCheck(
            name="tests",
            status="passed" if artifacts.test_report and artifacts.test_report.verdict == "passed" else "failed",
            detail="candidate verification passed" if artifacts.test_report and artifacts.test_report.verdict == "passed" else "candidate verification has not passed",
        ),
        EvaluationCheck(
            name="review",
            status="passed" if artifacts.review_report and artifacts.review_report.verdict == "approve" else "failed",
            detail="structural review approved" if artifacts.review_report and artifacts.review_report.verdict == "approve" else "structural review is not approved",
        ),
        EvaluationCheck(
            name="security",
            status="passed" if artifacts.security_scan and artifacts.security_scan.verdict == "passed" else "failed",
            detail="candidate security scan passed" if artifacts.security_scan and artifacts.security_scan.verdict == "passed" else "candidate security scan is not clean",
        ),
        EvaluationCheck(
            name="budget",
            status="passed" if not mission.usage.exceeded(mission.budget) else "failed",
            detail="mission remains within configured budgets" if not mission.usage.exceeded(mission.budget) else "mission exceeded: " + ", ".join(mission.usage.exceeded(mission.budget)),
        ),
        EvaluationCheck(
            name="audit",
            status="passed" if audit_valid else "failed",
            detail="mission audit chain matches its snapshot" if audit_valid else "mission audit chain is invalid",
        ),
    ]
    return checks


def _draft_pull_request(mission: MissionRecord, candidate: CandidateWorkspace, diff: str, approved_by: str) -> PullRequestDraft:
    files = changed_files(diff)
    title = mission.spec.title.strip()[:120]
    source = f"{mission.spec.source}:{mission.spec.source_id}".rstrip(":")
    body = (
        f"## Summary\n\n- Addresses {source}: {mission.spec.title}\n"
        f"- Prepared from isolated candidate `{candidate.id}` after approval by `{approved_by}`.\n\n"
        "## Validation\n\n"
        f"- Candidate verification: `{mission.artifacts.test_report.verdict if mission.artifacts.test_report else 'unknown'}`\n"
        f"- Structural review: `{mission.artifacts.review_report.verdict if mission.artifacts.review_report else 'unknown'}`\n"
        f"- Security scan: `{mission.artifacts.security_scan.verdict if mission.artifacts.security_scan else 'unknown'}`\n"
    )
    return PullRequestDraft(
        suggested_branch=f"ronin/{mission.id}",
        base_revision=candidate.base_revision,
        candidate_workspace_id=candidate.id,
        title=title or "Ronin mission update",
        body=body,
        files_changed=files,
        diff_digest=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
    )
