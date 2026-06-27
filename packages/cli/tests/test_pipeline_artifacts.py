"""Tests for Wave 5 structured handoff artifacts (pure, serializable, honest)."""
from __future__ import annotations

from ronin_cli.pipeline_artifacts import (
    AcceptanceCriterion,
    ArchitectPlan,
    ImplementationReport,
    ReviewReport,
    VerificationReport,
    artifact_for_role,
    extract_json_block,
    finalize_verdict,
    roll_up_criteria,
)


# --- JSON extraction ---------------------------------------------------------

def test_extract_fenced_json_block() -> None:
    text = "Here is my plan:\n```json\n{\"objective\": \"do x\"}\n```\nThanks."
    assert extract_json_block(text) == {"objective": "do x"}


def test_extract_prefers_last_block() -> None:
    text = "```json\n{\"a\": 1}\n```\nthen\n```json\n{\"a\": 2}\n```"
    assert extract_json_block(text) == {"a": 2}


def test_extract_bare_object_fallback() -> None:
    assert extract_json_block('noise {"k": "v"} more') == {"k": "v"}


def test_extract_none_when_no_json() -> None:
    assert extract_json_block("just prose, no json") is None
    assert extract_json_block("") is None
    assert extract_json_block(None) is None


# --- artifact shapes + round-trip --------------------------------------------

def test_architect_plan_shape_and_round_trip() -> None:
    plan = ArchitectPlan(
        task="add CSV export", objective="users can export to CSV",
        files_to_change=["export.py"], implementation_steps=["write writer", "wire button"],
        risks=["large files"], test_strategy="unit test the writer",
        acceptance_criteria=[AcceptanceCriterion(id="ac1", text="CSV downloads", status="unknown")],
    )
    data = plan.model_dump()
    assert data["kind"] == "architect_plan"
    assert data["files_to_change"] == ["export.py"]
    assert data["acceptance_criteria"][0]["text"] == "CSV downloads"
    again = ArchitectPlan.model_validate_json(plan.model_dump_json())
    assert again.implementation_steps == plan.implementation_steps


def test_implementation_report_shape() -> None:
    rep = ImplementationReport(completed_steps=["a"], files_changed=["x.py"],
                               diff_summary="+10 -2", confidence="medium",
                               unresolved_items=["needs review of edge case"])
    d = rep.model_dump()
    assert d["kind"] == "implementation_report"
    assert d["confidence"] == "medium" and d["files_changed"] == ["x.py"]


def test_review_report_shape() -> None:
    rep = ReviewReport(required_fixes=["handle None"], approval_recommendation="request_changes",
                       safety_concerns=["no input validation"])
    d = rep.model_dump()
    assert d["kind"] == "review_report"
    assert d["approval_recommendation"] == "request_changes"


def test_verification_report_shape() -> None:
    rep = VerificationReport(tests_run=["t1"], passed=3, failed=0, final_verdict="passed")
    d = rep.model_dump()
    assert d["kind"] == "verification_report"
    assert d["passed"] == 3 and d["final_verdict"] == "passed"


# --- explicit-unknown defaults (no faked fields) -----------------------------

def test_defaults_are_explicit_unknown() -> None:
    plan = ArchitectPlan()
    assert plan.objective == "" and plan.acceptance_criteria == []
    assert plan.parsed is False  # nothing parsed
    rep = VerificationReport()
    assert rep.final_verdict == "unknown"
    assert rep.passed is None and rep.failed is None  # not 0 — genuinely unknown


def test_from_text_parses_block_and_sets_flag() -> None:
    text = '```json\n{"objective": "ship", "files_to_change": ["a.py"]}\n```'
    plan = ArchitectPlan.from_text(text, summary="planned the work")
    assert plan.parsed is True
    assert plan.objective == "ship" and plan.files_to_change == ["a.py"]
    assert plan.summary == "planned the work"


def test_from_text_fallback_is_all_unknown() -> None:
    plan = ArchitectPlan.from_text("no json here, just talking", summary="")
    assert plan.parsed is False
    assert plan.files_to_change == [] and plan.objective == ""
    assert "no json here" in plan.summary  # keeps the prose as the summary


def test_artifact_for_role_mapping() -> None:
    assert artifact_for_role("architect") is ArchitectPlan
    assert artifact_for_role("implementer") is ImplementationReport
    assert artifact_for_role("reviewer") is ReviewReport
    assert artifact_for_role("tester") is VerificationReport
    assert artifact_for_role("verifier") is VerificationReport


# --- verdict honesty (never unknown -> passed) -------------------------------

def test_verdict_unmet_criterion_is_failed() -> None:
    rep = VerificationReport(final_verdict="passed",  # agent claims passed…
                             acceptance_criteria_status=[AcceptanceCriterion(text="x", status="unmet")])
    assert finalize_verdict(rep) == "failed"  # …but an unmet criterion overrides


def test_verdict_failed_tests_is_failed() -> None:
    assert finalize_verdict(VerificationReport(final_verdict="passed", failed=2)) == "failed"


def test_verdict_blocked_reason_is_blocked() -> None:
    assert finalize_verdict(VerificationReport(blocked_reason="missing dep")) == "blocked"


def test_verdict_unknown_criterion_keeps_unknown() -> None:
    rep = VerificationReport(final_verdict="passed",
                             acceptance_criteria_status=[AcceptanceCriterion(text="x", status="unknown")])
    assert finalize_verdict(rep) == "unknown"  # not silently upgraded


def test_verdict_passed_requires_evidence() -> None:
    # claims passed but no tests ran and no criteria → cannot confirm
    assert finalize_verdict(VerificationReport(final_verdict="passed")) == "unknown"
    # passed WITH tests run → passed
    assert finalize_verdict(VerificationReport(final_verdict="passed", tests_run=["t1"])) == "passed"
    # passed with all criteria met → passed
    rep = VerificationReport(final_verdict="passed",
                             acceptance_criteria_status=[AcceptanceCriterion(text="x", status="met")])
    assert finalize_verdict(rep) == "passed"


def test_roll_up_criteria_buckets() -> None:
    rep = VerificationReport(acceptance_criteria_status=[
        AcceptanceCriterion(text="a", status="met"),
        AcceptanceCriterion(text="b", status="unmet"),
        AcceptanceCriterion(text="c", status="unknown"),
        AcceptanceCriterion(text="d", status="not_applicable"),
    ])
    buckets = roll_up_criteria(rep)
    assert buckets["met"] == ["a"] and buckets["unmet"] == ["b"]
    assert buckets["unknown"] == ["c"] and buckets["not_applicable"] == ["d"]
    assert roll_up_criteria(None) == {"met": [], "unmet": [], "unknown": [], "not_applicable": []}
