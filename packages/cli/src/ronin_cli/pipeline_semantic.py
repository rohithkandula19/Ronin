"""Optional semantic contract check for the role pipeline.

Where :mod:`pipeline_contract` is *structural* (do the artifacts' fields line
up?), this is *semantic*: a cheap read-only model pass that judges whether the
implementation actually did what the architect's plan + acceptance criteria
asked for. It is advisory by default and **conservative** — it never claims a
pass on weak evidence (no JSON / all-unknown stays ``unknown``).

The model call is injected (``judge_runner``) so the logic is pure and the tests
need no network; production wires a free/read-only model.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .pipeline_artifacts import (
    ArchitectPlan,
    ImplementationReport,
    ReviewReport,
    extract_json_block,
)

STATUSES = ("passed", "warning", "failed", "unknown")


class SemanticContractReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    objective_alignment: str = "unknown"   # aligned / partially_aligned / misaligned / unknown
    acceptance_alignment: str = "unknown"  # aligned / partially_aligned / misaligned / unknown
    scope_creep_risk: str = "unknown"      # none / low / medium / high / unknown
    missing_plan_items: list[str] = Field(default_factory=list)
    unexpected_changes: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    final_semantic_status: str = "unknown"  # passed / warning / failed / unknown
    summary: str = ""
    parsed: bool = False


def finalize_semantic_status(report: SemanticContractReport) -> str:
    """Conservative status — never 'passed' without real, aligned evidence."""
    obj, acc = report.objective_alignment, report.acceptance_alignment
    if obj == "misaligned" or acc == "misaligned":
        return "failed"
    if report.scope_creep_risk == "high":
        return "failed"
    if (report.missing_plan_items or report.unexpected_changes
            or report.scope_creep_risk == "medium"
            or obj == "partially_aligned" or acc == "partially_aligned"):
        return "warning"
    if obj == "aligned" and acc == "aligned" and report.evidence:
        return "passed"
    return "unknown"


def build_semantic_prompt(plan: ArchitectPlan | None, impl: ImplementationReport | None,
                          review: ReviewReport | None) -> str:
    lines = ["You are a READ-ONLY semantic reviewer. Judge whether the implementation "
             "actually fulfils the plan. Do NOT edit anything.", ""]
    if plan is not None:
        lines += [f"PLAN objective: {plan.objective or '(none stated)'}",
                  f"PLAN files_to_change: {', '.join(plan.files_to_change) or '(none)'}",
                  f"PLAN steps: {' | '.join(plan.implementation_steps) or '(none)'}",
                  "PLAN acceptance criteria: "
                  + (" | ".join(c.text for c in plan.acceptance_criteria) or "(none)"),
                  f"PLAN risks: {', '.join(plan.risks) or '(none)'}", ""]
    if impl is not None:
        lines += [f"IMPLEMENTATION files_changed: {', '.join(impl.files_changed) or '(none)'}",
                  f"IMPLEMENTATION diff_summary: {impl.diff_summary or '(none)'}",
                  f"IMPLEMENTATION completed_steps: {' | '.join(impl.completed_steps) or '(none)'}", ""]
    if review is not None and review.required_fixes:
        lines.append(f"REVIEW required_fixes: {' | '.join(review.required_fixes)}")
        lines.append("")
    lines += [
        "Answer: did the diff implement the planned objective? did changed files match "
        "the plan? were the acceptance criteria addressed? is there obvious scope creep? "
        "any unresolved risks? Be honest about uncertainty — say 'unknown' when the "
        "evidence is weak.",
        "",
        "End with a single ```json block of this shape (use explicit unknowns):",
        '{"objective_alignment": "aligned|partially_aligned|misaligned|unknown", '
        '"acceptance_alignment": "aligned|partially_aligned|misaligned|unknown", '
        '"scope_creep_risk": "none|low|medium|high|unknown", '
        '"missing_plan_items": [], "unexpected_changes": [], "evidence": []}',
    ]
    return "\n".join(lines)


def check_semantic_contract(plan: ArchitectPlan | None, impl: ImplementationReport | None,
                            review: ReviewReport | None, *, judge_runner) -> SemanticContractReport:
    """Run the injected ``judge_runner(prompt) -> str`` and build a conservative
    report. Returns an all-unknown report (never raises) when there's no runner,
    no parseable JSON, or the runner errors."""
    if judge_runner is None:
        return SemanticContractReport(final_semantic_status="unknown",
                                      summary="semantic check not run (no judge)")
    prompt = build_semantic_prompt(plan, impl, review)
    try:
        reply = judge_runner(prompt) or ""
    except Exception as exc:  # noqa: BLE001 — degrade to unknown, never crash the run
        return SemanticContractReport(final_semantic_status="unknown",
                                      summary=f"semantic check errored: {exc}")
    data = extract_json_block(reply)
    if not data:
        return SemanticContractReport(final_semantic_status="unknown", parsed=False,
                                      summary=(reply.strip().splitlines() or ["(no output)"])[0][:200])
    try:
        report = SemanticContractReport.model_validate(data)
    except Exception:  # noqa: BLE001
        report = SemanticContractReport()
    report.parsed = True
    report.summary = (reply.strip().splitlines() or [""])[0][:200]
    report.final_semantic_status = finalize_semantic_status(report)
    return report
