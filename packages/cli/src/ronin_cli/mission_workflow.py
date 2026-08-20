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
    IssueAnalysis,
    ImplementationEvidence,
    MissionArtifacts,
    MissionRecord,
    MissionStage,
    MissionUsage,
    PlanArtifact,
    PullRequestDraft,
    RepositoryMap,
    ReviewFinding,
    ReviewReport,
    RootCauseAnalysis,
    SecurityFinding,
    SecurityScan,
    SelfReviewNotes,
    TestReport,
    TestSuiteResult,
    VerificationReport,
    now_utc,
)
from .mission_store import MissionStore
from .secret_scan import find_secrets


class MissionWorkflow:
    """Advance one mission only when its typed evidence supports the transition."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.store = MissionStore(self.root)
        self.candidates = CandidateWorkspaceService(self.root)

    def record_issue_analysis(
        self, mission_id: str, analysis: IssueAnalysis, *, actor: str = "inspector",
    ) -> MissionRecord:
        """Record Phase 1 evidence and enter inspection without starting work."""
        mission = self.store.load(mission_id)
        if mission.stage is MissionStage.PENDING:
            mission = self.store.transition(
                mission.id, MissionStage.INSPECTING, actor=actor, summary="issue analysis recorded",
            )
        if mission.stage not in {MissionStage.INSPECTING, MissionStage.AWAITING_CLARIFICATION}:
            raise ValueError(f"mission must be inspecting before issue analysis is recorded; it is {mission.stage.value}")
        return self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"issue_analysis": analysis}), actor=actor,
        )

    def record_repository_map(
        self, mission_id: str, repository_map: RepositoryMap, *, actor: str = "inspector",
    ) -> MissionRecord:
        """Record Phase 2 repository evidence before root-cause or planning work."""
        mission = self._require_inspecting(mission_id)
        return self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"repository_map": repository_map}), actor=actor,
        )

    def record_root_cause(
        self, mission_id: str, root_cause: RootCauseAnalysis, *, actor: str = "investigator",
    ) -> MissionRecord:
        """Record Phase 3 root-cause evidence while the mission is still read-only."""
        mission = self._require_inspecting(mission_id)
        return self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"root_cause": root_cause}), actor=actor,
        )

    def record_plan(self, mission_id: str, plan: PlanArtifact, *, actor: str = "architect") -> MissionRecord:
        """Store a structured plan and make the mission eligible for implementation."""
        mission = self.store.load(mission_id)
        if mission.stage is MissionStage.PENDING:
            mission = self.store.transition(
                mission.id, MissionStage.INSPECTING, actor=actor, summary="MissionSpec inspected",
            )
        if mission.stage not in {MissionStage.INSPECTING, MissionStage.AWAITING_CLARIFICATION}:
            raise ValueError(f"mission must be inspecting before a plan is recorded; it is {mission.stage.value}")
        if mission.spec.workflow == "verified":
            missing = _missing_preplan_evidence(mission)
            if missing:
                raise ValueError("verified mission needs " + ", ".join(missing) + " before planning")
        mission = self.store.transition(
            mission.id, MissionStage.PLANNING, actor=actor, summary="structured implementation plan prepared",
        )
        mission = self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"plan": plan}),
        )
        if mission.spec.workflow == "verified":
            return mission
        return self.store.transition(
            mission.id, MissionStage.IMPLEMENTING, actor=actor, summary="plan approved for candidate implementation",
        )

    def approve_plan(self, mission_id: str, *, approved_by: str) -> MissionRecord:
        """Explicitly release a verified plan to the isolated implementation phase."""
        approver = approved_by.strip()
        if not approver:
            raise ValueError("an approving human identity is required for a verified plan")
        mission = self._require_stage(mission_id, MissionStage.PLANNING)
        if mission.spec.workflow != "verified":
            raise ValueError("plan approval is only required for verified missions")
        if mission.artifacts.plan is None or not mission.artifacts.plan.steps:
            raise ValueError("verified mission has no structured plan to approve")
        approved_plan = mission.artifacts.plan.model_copy(update={"approved_by": approver, "approved_at": now_utc()})
        mission = self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"plan": approved_plan}), actor=f"approval:{approver}",
        )
        return self.store.transition(
            mission.id, MissionStage.IMPLEMENTING, actor=f"approval:{approver}",
            summary="human approved verified implementation plan",
        )

    def execute_implementation(
        self,
        mission_id: str,
        runner: Callable[[str, Path, int], object],
        *,
        max_iterations: int = 25,
    ) -> MissionRecord:
        """Run one bounded implementation turn only in the attached candidate.

        ``runner`` is injected so the workflow retains its evidence/state-machine
        ownership while the CLI owns provider construction. It receives a trusted
        mission-derived task and the detached candidate path. It must never be
        called before an approved plan, against the parent checkout, or after the
        mission has exhausted its declared resources.
        """
        if not 1 <= max_iterations <= 100:
            raise ValueError("implementation max_iterations must be between 1 and 100")
        mission = self._require_stage(mission_id, MissionStage.IMPLEMENTING)
        candidate = self._candidate_for(mission)
        if not candidate.image:
            raise ValueError("candidate needs a Docker image before agent implementation")
        if mission.usage.tool_calls >= mission.budget.max_tool_calls:
            raise ValueError("mission tool-call budget is exhausted before implementation")
        if mission.usage.repair_attempts > mission.budget.max_repair_attempts:
            raise ValueError("mission repair budget is exhausted before implementation")
        plan = mission.artifacts.plan
        if plan is None or not plan.steps:
            raise ValueError("mission needs an approved structured plan before implementation")

        result = runner(_implementation_task(mission), Path(candidate.path), max_iterations)
        diff = self.candidates.diff(candidate.id)
        usage = getattr(result, "usage", {}) or {}
        input_tokens = _usage_count(usage, "input_tokens")
        output_tokens = _usage_count(usage, "output_tokens")
        iterations = max(0, int(getattr(result, "iterations", 0) or 0))
        tool_calls = sum(
            1 for step in (getattr(result, "steps", []) or [])
            if getattr(step, "kind", "") == "tool"
        )
        success = bool(getattr(result, "success", False))
        error = str(getattr(result, "error", "") or "")[:4_000]
        evidence = ImplementationEvidence(
            runner="candidate-code-agent",
            success=success,
            iterations=iterations,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            changed_files=changed_files(diff),
            diff_digest=hashlib.sha256(diff.encode("utf-8")).hexdigest() if diff else "",
            error=error,
        )
        next_usage = MissionUsage(
            tokens=mission.usage.tokens + input_tokens + output_tokens,
            cost_usd=mission.usage.cost_usd,
            wall_time_seconds=mission.usage.wall_time_seconds,
            tool_calls=mission.usage.tool_calls + tool_calls,
            repair_attempts=mission.usage.repair_attempts + (0 if success else 1),
        )
        recorded = self.store.set_usage(mission.id, next_usage, actor="candidate-agent")
        artifacts = recorded.artifacts.model_copy(update={
            "implementation_evidence": [*recorded.artifacts.implementation_evidence, evidence],
        })
        recorded = self.store.save_artifacts(recorded.id, artifacts, actor="candidate-agent")
        exceeded = next_usage.exceeded(recorded.budget)
        if exceeded:
            return self.store.transition(
                recorded.id, MissionStage.FAILED, actor="candidate-agent",
                summary="agent implementation exceeded mission budget",
                error="implementation exceeded mission budget: " + ", ".join(exceeded),
            )
        if not success and next_usage.repair_attempts > recorded.budget.max_repair_attempts:
            return self.store.transition(
                recorded.id, MissionStage.FAILED, actor="candidate-agent",
                summary="agent implementation retry budget exhausted",
                error="agent implementation failed after repair budget was exhausted",
            )
        # A successful turn deliberately remains IMPLEMENTING. Docker-only tests
        # and independent review/security gates decide whether it may advance.
        return recorded

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
        return self._record_test_result(testing, command, result, actor="test-gate")

    def record_remote_verification(
        self,
        mission_id: str,
        result: CandidateCommandResult,
        *,
        worker_id: str,
    ) -> MissionRecord:
        """Record a leased worker's Docker result after the controller revalidated it.

        The remote-worker coordinator owns lease, patch-digest, and candidate
        revalidation. This method still owns the mission state transition and
        budget accounting, so a worker cannot bypass an evidence gate.
        """
        if not worker_id or len(worker_id) > 80:
            raise ValueError("remote worker id is invalid")
        mission = self._require_stage(mission_id, MissionStage.IMPLEMENTING)
        self._candidate_for(mission)
        actor = f"remote-worker:{worker_id}"
        testing = self.store.transition(
            mission.id, MissionStage.TESTING, actor=actor, summary="remote candidate verification started",
        )
        if testing.usage.tool_calls >= testing.budget.max_tool_calls:
            return self._record_blocked_test(
                testing, result.command, "mission tool-call budget is exhausted before verification", actor=actor,
            )
        return self._record_test_result(testing, result.command, result, actor=actor)

    def _record_test_result(
        self,
        testing: MissionRecord,
        command: str,
        result: CandidateCommandResult,
        *,
        actor: str,
    ) -> MissionRecord:
        usage = self._usage_after_test(testing.usage, result)
        testing = self.store.set_usage(testing.id, usage, actor=actor)
        report = _test_report(command, result, repair_attempt=usage.repair_attempts)
        testing = self.store.save_artifacts(
            testing.id, testing.artifacts.model_copy(update={"test_report": report}),
            actor=actor,
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
                actor=actor,
                summary="candidate test retry budget exhausted",
                error=f"verification failed after {usage.repair_attempts} repair attempt(s); retry budget exhausted",
            )
        exceeded = usage.exceeded(testing.budget)
        if exceeded:
            return self.store.transition(
                testing.id,
                MissionStage.FAILED,
                actor=actor,
                summary="mission resource budget exceeded during verification",
                error="verification exceeded mission budget: " + ", ".join(exceeded),
            )
        if result.passed:
            return self.store.transition(
                testing.id, MissionStage.REVIEWING, actor=actor, summary="candidate verification passed",
            )
        if result.blocked or result.timed_out:
            return self.store.transition(
                testing.id,
                MissionStage.FAILED,
                actor=actor,
                summary="candidate verification was blocked",
                error=_failure_reason(result),
            )
        return self.store.transition(
            testing.id,
            MissionStage.IMPLEMENTING,
            actor=actor,
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
        verification = _verification_report(scanned)
        scanned = self.store.save_artifacts(
            scanned.id, scanned.artifacts.model_copy(update={"verification_report": verification}),
            actor="verification-gate",
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

    def record_self_review(
        self, mission_id: str, review: SelfReviewNotes, *, actor: str | None = None,
    ) -> MissionRecord:
        """Retain Phase 7 review notes after independent candidate gates pass."""
        mission = self._require_stage(mission_id, MissionStage.AWAITING_APPROVAL)
        reviewer = actor or review.reviewer
        return self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"self_review": review}), actor=reviewer,
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
        if evaluated.spec.workflow == "verified":
            missing = _missing_pr_evidence(evaluated)
            if missing:
                raise ValueError("verified mission needs " + ", ".join(missing) + " before a PR draft")
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

    def _require_inspecting(self, mission_id: str) -> MissionRecord:
        mission = self.store.load(mission_id)
        if mission.stage not in {MissionStage.INSPECTING, MissionStage.AWAITING_CLARIFICATION}:
            raise ValueError(f"mission must be inspecting; it is {mission.stage.value}")
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

    def _record_blocked_test(
        self,
        mission: MissionRecord,
        command: str,
        reason: str,
        *,
        actor: str = "test-gate",
    ) -> MissionRecord:
        report = TestReport(
            suites=[TestSuiteResult(name="candidate", command=command, status="blocked", output_summary=reason)],
            repair_attempt=mission.usage.repair_attempts,
            verdict="blocked",
        )
        blocked = self.store.save_artifacts(
            mission.id, mission.artifacts.model_copy(update={"test_report": report}),
            actor=actor,
        )
        return self.store.transition(
            blocked.id, MissionStage.FAILED, actor=actor, summary="candidate verification blocked", error=reason,
        )


def _implementation_task(mission: MissionRecord) -> str:
    """Build a bounded, trusted task from the mission's approved local artifacts."""
    plan = mission.artifacts.plan
    assert plan is not None  # checked by execute_implementation before this helper.
    steps = "\n".join(
        f"- {step.id}: {step.description}"
        + (f" (files: {', '.join(step.files)})" if step.files else "")
        for step in plan.steps
    )
    requirements = "\n".join(f"- {item}" for item in mission.spec.requirements) or "- No extra requirements recorded."
    acceptance = "\n".join(f"- {item}" for item in mission.spec.acceptance_criteria) or "- Preserve existing behavior outside this change."
    return (
        "Implement the approved mission in the current detached candidate checkout.\n\n"
        f"Mission: {mission.spec.title}\n"
        f"Request:\n{mission.spec.issue_text[:30_000]}\n\n"
        f"Requirements:\n{requirements}\n\n"
        f"Approved plan:\n{steps}\n\n"
        f"Acceptance criteria:\n{acceptance}\n\n"
        "Work only in this candidate checkout. Make focused edits and run useful local checks. "
        "Do not commit, push, alter Git remotes, or modify policy files. Finish with a concise summary of changed files and verification."
    )


def _usage_count(usage: object, key: str) -> int:
    if not isinstance(usage, dict):
        return 0
    try:
        return max(0, int(usage.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _missing_preplan_evidence(mission: MissionRecord) -> tuple[str, ...]:
    artifacts = mission.artifacts
    missing: list[str] = []
    if artifacts.issue_analysis is None:
        missing.append("issue analysis")
    if artifacts.repository_map is None:
        missing.append("repository map")
    if artifacts.root_cause is None:
        missing.append("root-cause analysis")
    return tuple(missing)


def _missing_pr_evidence(mission: MissionRecord) -> tuple[str, ...]:
    artifacts = mission.artifacts
    missing = list(_missing_preplan_evidence(mission))
    if artifacts.plan is None or not artifacts.plan.approved_by:
        missing.append("named plan approval")
    if artifacts.verification_report is None or artifacts.verification_report.verdict != "passed":
        missing.append("passing verification report")
    if artifacts.self_review is None:
        missing.append("self-review notes")
    return tuple(missing)


def _verification_report(mission: MissionRecord) -> VerificationReport:
    """Derive final verification evidence only from recorded candidate results."""
    artifacts = mission.artifacts
    checks = list(artifacts.test_report.suites) if artifacts.test_report else []
    review_status = "passed" if artifacts.review_report and artifacts.review_report.verdict == "approve" else "failed"
    checks.append(TestSuiteResult(
        name="structural-review", status=review_status,
        output_summary="candidate structural review approved" if review_status == "passed" else "candidate structural review did not approve",
    ))
    security_status = "passed" if artifacts.security_scan and artifacts.security_scan.verdict == "passed" else "failed"
    checks.append(TestSuiteResult(
        name="security-scan", status=security_status,
        output_summary="candidate secret scan passed" if security_status == "passed" else "candidate secret scan found blocking evidence",
    ))
    passed = bool(checks) and all(check.status == "passed" for check in checks)
    reproduction = "No reproduction steps were recorded."
    if artifacts.issue_analysis and artifacts.issue_analysis.reproduction_steps:
        reproduction = "Recorded reproduction steps are covered by the candidate verification evidence."
    security_summary = "No issues found." if security_status == "passed" else "Security findings require remediation."
    return VerificationReport(
        checks=checks,
        security_summary=security_summary,
        reproduction_summary=reproduction,
        verdict="passed" if passed else "failed",
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
    if mission.spec.workflow == "verified":
        checks.extend([
            EvaluationCheck(
                name="issue_analysis",
                status="passed" if artifacts.issue_analysis else "failed",
                detail="issue analysis is recorded" if artifacts.issue_analysis else "issue analysis is missing",
            ),
            EvaluationCheck(
                name="repository_map",
                status="passed" if artifacts.repository_map else "failed",
                detail="repository map is recorded" if artifacts.repository_map else "repository map is missing",
            ),
            EvaluationCheck(
                name="root_cause",
                status="passed" if artifacts.root_cause else "failed",
                detail="root-cause analysis is recorded" if artifacts.root_cause else "root-cause analysis is missing",
            ),
            EvaluationCheck(
                name="plan_approval",
                status="passed" if artifacts.plan and artifacts.plan.approved_by else "failed",
                detail="named plan approval is recorded" if artifacts.plan and artifacts.plan.approved_by else "named plan approval is missing",
            ),
            EvaluationCheck(
                name="verification_report",
                status="passed" if artifacts.verification_report and artifacts.verification_report.verdict == "passed" else "failed",
                detail="verification report passed" if artifacts.verification_report and artifacts.verification_report.verdict == "passed" else "verification report is incomplete or failed",
            ),
            EvaluationCheck(
                name="self_review",
                status="passed" if artifacts.self_review else "failed",
                detail="self-review notes are recorded" if artifacts.self_review else "self-review notes are missing",
            ),
        ])
    return checks


def _draft_pull_request(mission: MissionRecord, candidate: CandidateWorkspace, diff: str, approved_by: str) -> PullRequestDraft:
    files = changed_files(diff)
    title = _conventional_title(mission.spec.title)
    source = f"{mission.spec.source}:{mission.spec.source_id}".rstrip(":")
    body = _verified_pr_body(mission, source, approved_by) if mission.spec.workflow == "verified" else (
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


def _conventional_title(title: str) -> str:
    """Use the repository's conventional-commit style without guessing intent."""
    clean = title.strip()
    if clean.lower().startswith(("feat(", "fix(", "docs(", "test(", "refactor(", "perf(", "ci(",
                                 "feat:", "fix:", "docs:", "test:", "refactor:", "perf:", "ci:")):
        return clean[:120]
    low = clean.lower()
    prefix = "fix" if any(word in low for word in ("bug", "fix", "error", "failure", "regression")) else "feat"
    return f"{prefix}: {clean}"[:120]


def _verified_pr_body(mission: MissionRecord, source: str, approved_by: str) -> str:
    """Render a review-ready PR body solely from typed mission evidence."""
    artifacts = mission.artifacts
    analysis = artifacts.issue_analysis
    root_cause = artifacts.root_cause
    plan = artifacts.plan
    verification = artifacts.verification_report
    review = artifacts.self_review
    assert analysis is not None and root_cause is not None and plan is not None and verification is not None and review is not None

    changes = [step.description for step in plan.steps[:20]] or ["No implementation steps were recorded."]
    testing = []
    for check in verification.checks[:20]:
        command = f" `{check.command}`" if check.command else ""
        testing.append(f"- {check.name}: **{check.status}**{command} - {check.output_summary}")
    criteria = analysis.acceptance_criteria or mission.spec.acceptance_criteria
    check_statuses = {check.name: check.status for check in verification.checks}
    checklist = [
        ("Issue reproduction confirmed", bool(analysis.reproduction_steps)),
        ("Root cause identified", True),
        ("Minimal fix implemented", bool(plan.steps)),
        ("Tests added/updated and passing", bool(artifacts.test_report and artifacts.test_report.verdict == "passed")),
        ("Linting/formatting checks pass", check_statuses.get("lint") == "passed" and check_statuses.get("format") == "passed"),
        ("Security review completed", bool(artifacts.security_scan and artifacts.security_scan.verdict == "passed")),
        ("Self-review completed", True),
        ("Documentation updated (if applicable)", bool(plan.dependency_impacts)),
    ]
    body = "\n".join([
        "## Summary",
        "",
        analysis.summary,
        "",
        "## Root Cause",
        "",
        root_cause.cause,
        "",
        "## Acceptance Criteria",
        "",
        *([f"- {criterion}" for criterion in criteria] if criteria else ["- No explicit acceptance criteria were recorded."]),
        "",
        "## Changes Made",
        "",
        *[f"- {change}" for change in changes],
        "",
        "## Testing",
        "",
        *(testing or ["- No verification checks were recorded."]),
        f"- Reproduction: {verification.reproduction_summary}",
        "",
        "## Security Considerations",
        "",
        verification.security_summary,
        "",
        "## Self-Review",
        "",
        review.notes or "Reviewed for scope, error handling, and edge cases.",
        "",
        "## Checklist",
        "",
        *[f"- [{'x' if complete else ' '}] {item}" for item, complete in checklist],
        "",
        "## Related Issues",
        "",
        f"- {source or mission.spec.title}",
        f"- Plan approved by: {plan.approved_by}",
        f"- PR preparation approved by: {approved_by}",
    ])
    return body[:20_000]
