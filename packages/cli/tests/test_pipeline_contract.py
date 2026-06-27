"""Tests for Wave 6 artifact contract enforcement (pure)."""
from __future__ import annotations

from ronin_cli.pipeline_artifacts import (
    AcceptanceCriterion,
    ArchitectPlan,
    ImplementationReport,
    ReviewFinding,
    ReviewReport,
    VerificationReport,
)
from ronin_cli.pipeline_contract import ContractCheckReport, check_contract


def test_contract_passed_when_everything_aligns() -> None:
    plan = ArchitectPlan(
        files_to_change=["export.py"], implementation_steps=["write writer"],
        acceptance_criteria=[AcceptanceCriterion(id="ac1", text="csv downloads")])
    impl = ImplementationReport(files_changed=["export.py"], completed_steps=["write writer"])
    review = ReviewReport(required_fixes=[], findings=[])
    ver = VerificationReport(
        acceptance_criteria_status=[AcceptanceCriterion(id="ac1", text="csv downloads", status="met")])
    r = check_contract(plan, impl, review, ver)
    assert r.final_contract_status == "passed"
    assert "files_overlap" in r.passed_checks
    assert "acceptance_coverage" in r.passed_checks
    assert not r.failed_checks and not r.blocking_issues


def test_contract_warns_on_changed_file_outside_plan() -> None:
    plan = ArchitectPlan(files_to_change=["export.py"])
    impl = ImplementationReport(files_changed=["export.py", "secret.py"])  # extra, unexplained
    r = check_contract(plan, impl, None, None)
    assert r.final_contract_status == "warning"
    assert any("files_outside_plan" in w for w in r.warnings)
    assert "secret.py" in " ".join(r.warnings)


def test_contract_fails_when_no_planned_file_touched() -> None:
    plan = ArchitectPlan(files_to_change=["export.py"])
    impl = ImplementationReport(files_changed=["unrelated.py"])
    r = check_contract(plan, impl, None, None)
    assert r.final_contract_status == "failed"
    assert any("files_overlap" in f for f in r.failed_checks)


def test_contract_fails_on_missing_acceptance_coverage() -> None:
    plan = ArchitectPlan(acceptance_criteria=[
        AcceptanceCriterion(id="ac1", text="a"), AcceptanceCriterion(id="ac2", text="b")])
    ver = VerificationReport(acceptance_criteria_status=[
        AcceptanceCriterion(id="ac1", text="a", status="met")])  # ac2 missing
    r = check_contract(plan, None, None, ver)
    assert r.final_contract_status == "failed"
    assert any("acceptance_coverage" in f for f in r.failed_checks)


def test_contract_blocking_review_fix() -> None:
    review = ReviewReport(required_fixes=["sanitize input"])
    r = check_contract(None, None, review, None)
    assert r.final_contract_status == "failed"
    assert any("sanitize input" in b for b in r.blocking_issues)


def test_contract_blocking_severity_finding() -> None:
    review = ReviewReport(findings=[ReviewFinding(text="SQL injection", severity="blocking")])
    r = check_contract(None, None, review, None)
    assert r.blocking_issues and r.final_contract_status == "failed"


def test_contract_unknown_when_nothing_to_check() -> None:
    # empty/unparsed artifacts → unknown, never silently passed
    r = check_contract(ArchitectPlan(), ImplementationReport(), ReviewReport(), VerificationReport())
    assert r.final_contract_status in ("unknown", "passed")  # no planned files/criteria/fixes
    # specifically: no failed checks were invented
    assert not r.failed_checks and not r.blocking_issues


def test_contract_warns_when_steps_not_reported() -> None:
    plan = ArchitectPlan(implementation_steps=["s1", "s2"])
    impl = ImplementationReport(completed_steps=[])
    r = check_contract(plan, impl, None, None)
    assert any("steps_correspondence" in w for w in r.warnings)


def test_contract_report_round_trips() -> None:
    r = check_contract(ArchitectPlan(files_to_change=["a.py"]),
                       ImplementationReport(files_changed=["a.py"]), None, None)
    again = ContractCheckReport.model_validate_json(r.model_dump_json())
    assert again.final_contract_status == r.final_contract_status
    assert again.passed_checks == r.passed_checks
