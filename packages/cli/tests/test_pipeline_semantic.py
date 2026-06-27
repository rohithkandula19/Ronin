"""Tests for Wave 7 semantic contract check (pure, injected judge)."""
from __future__ import annotations

import json

from ronin_cli.pipeline_artifacts import ArchitectPlan, ImplementationReport
from ronin_cli.pipeline_semantic import (
    SemanticContractReport,
    build_semantic_prompt,
    check_semantic_contract,
    finalize_semantic_status,
)


def _judge(payload: dict):
    return lambda prompt: f"My analysis…\n```json\n{json.dumps(payload)}\n```"


def test_report_round_trips() -> None:
    r = SemanticContractReport(objective_alignment="aligned", final_semantic_status="passed",
                               evidence=["the diff edits export.py as planned"])
    again = SemanticContractReport.model_validate_json(r.model_dump_json())
    assert again.objective_alignment == "aligned"
    assert again.evidence == r.evidence


def test_passed_case() -> None:
    judge = _judge({"objective_alignment": "aligned", "acceptance_alignment": "aligned",
                    "scope_creep_risk": "none", "missing_plan_items": [],
                    "unexpected_changes": [], "evidence": ["export.py implements CSV"]})
    # 'passed' requires a full diff to look at (Wave 8 honesty)
    full_diff = {"captured": True, "full_diff_available": True, "truncated": False}
    r = check_semantic_contract(ArchitectPlan(objective="csv"), ImplementationReport(),
                                None, judge_runner=judge, diff_evidence=full_diff)
    assert r.parsed is True and r.final_semantic_status == "passed"


def test_warning_case_scope_creep_medium() -> None:
    judge = _judge({"objective_alignment": "aligned", "acceptance_alignment": "partially_aligned",
                    "scope_creep_risk": "medium", "missing_plan_items": ["edge cases"],
                    "evidence": ["x"]})
    r = check_semantic_contract(None, None, None, judge_runner=judge)
    assert r.final_semantic_status == "warning"


def test_failed_case_misaligned() -> None:
    judge = _judge({"objective_alignment": "misaligned", "acceptance_alignment": "unknown",
                    "evidence": ["the diff touches unrelated files"]})
    r = check_semantic_contract(None, None, None, judge_runner=judge)
    assert r.final_semantic_status == "failed"


def test_unknown_when_no_json() -> None:
    r = check_semantic_contract(None, None, None, judge_runner=lambda p: "just prose, no json")
    assert r.parsed is False and r.final_semantic_status == "unknown"


def test_unknown_when_no_judge() -> None:
    r = check_semantic_contract(None, None, None, judge_runner=None)
    assert r.final_semantic_status == "unknown"
    assert "no judge" in r.summary


def test_judge_error_degrades_to_unknown() -> None:
    def boom(prompt):
        raise RuntimeError("model down")
    r = check_semantic_contract(None, None, None, judge_runner=boom)
    assert r.final_semantic_status == "unknown" and "errored" in r.summary


def test_finalize_branches() -> None:
    assert finalize_semantic_status(SemanticContractReport(objective_alignment="misaligned")) == "failed"
    assert finalize_semantic_status(SemanticContractReport(scope_creep_risk="high")) == "failed"
    assert finalize_semantic_status(SemanticContractReport(unexpected_changes=["x"])) == "warning"
    assert finalize_semantic_status(
        SemanticContractReport(objective_alignment="aligned", acceptance_alignment="aligned",
                               evidence=["e"])) == "passed"
    # aligned but NO evidence → not passed (conservative)
    assert finalize_semantic_status(
        SemanticContractReport(objective_alignment="aligned", acceptance_alignment="aligned")) == "unknown"


def test_prompt_includes_objective_and_asks_for_json() -> None:
    p = build_semantic_prompt(ArchitectPlan(objective="add CSV export"), ImplementationReport(), None)
    assert "add CSV export" in p
    assert "```json" in p and "scope_creep_risk" in p


# --- Wave 8: full-diff semantic evidence + truncation honesty ----------------

def test_prompt_includes_actual_diff() -> None:
    de = {"captured": True, "full_diff_available": True, "truncated": False,
          "files_changed": ["export.py"], "additions": 12, "deletions": 1,
          "diff_excerpt": "diff --git a/export.py b/export.py\n+def to_csv():"}
    p = build_semantic_prompt(ArchitectPlan(objective="csv"), ImplementationReport(), None, de)
    assert "ACTUAL DIFF" in p and "export.py" in p and "to_csv" in p


def test_prompt_notes_missing_diff() -> None:
    p = build_semantic_prompt(ArchitectPlan(), ImplementationReport(), None,
                              {"captured": False})
    assert "not available" in p and "Do NOT" in p


def test_passed_downgraded_to_unknown_without_diff() -> None:
    judge = _judge({"objective_alignment": "aligned", "acceptance_alignment": "aligned",
                    "scope_creep_risk": "none", "evidence": ["looks right"]})
    # no diff evidence → a 'passed' judgement must not stand
    r = check_semantic_contract(ArchitectPlan(), ImplementationReport(), None,
                                judge_runner=judge, diff_evidence={"captured": False})
    assert r.final_semantic_status == "unknown"


def test_passed_downgraded_to_warning_when_truncated() -> None:
    judge = _judge({"objective_alignment": "aligned", "acceptance_alignment": "aligned",
                    "scope_creep_risk": "none", "evidence": ["aligned"]})
    r = check_semantic_contract(ArchitectPlan(), ImplementationReport(), None,
                                judge_runner=judge,
                                diff_evidence={"captured": True, "full_diff_available": True,
                                               "truncated": True})
    assert r.final_semantic_status == "warning"


def test_passed_stands_with_full_diff() -> None:
    judge = _judge({"objective_alignment": "aligned", "acceptance_alignment": "aligned",
                    "scope_creep_risk": "none", "evidence": ["the diff adds to_csv"]})
    r = check_semantic_contract(ArchitectPlan(), ImplementationReport(), None,
                                judge_runner=judge,
                                diff_evidence={"captured": True, "full_diff_available": True,
                                               "truncated": False})
    assert r.final_semantic_status == "passed"


def test_failed_stands_regardless_of_diff() -> None:
    # a clear contradiction fails even without a full diff
    judge = _judge({"objective_alignment": "misaligned", "evidence": ["unrelated changes"]})
    r = check_semantic_contract(None, None, None, judge_runner=judge,
                                diff_evidence={"captured": False})
    assert r.final_semantic_status == "failed"
