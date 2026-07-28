"""Workflow contracts for orchestrated specialist teams.

The planner remains free to decompose a goal into domain-specific subtasks, but
it no longer gets to omit the engineering handoffs that make a result
trustworthy.  A contract declares required roles, role-level dependencies, and
independent acceptance roles; the agent-patterns orchestrator validates the
resulting plan before any sub-agent starts.

This module intentionally reuses the pipeline's typed artifacts and structural
contract checker.  Orchestrated agents may still answer in prose, so an
unparsed artifact is reported as ``unknown`` rather than manufactured into a
pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .pipeline_artifacts import (
    ArchitectPlan,
    ImplementationReport,
    ReviewReport,
    VerificationReport,
)
from .pipeline_contract import check_contract


@dataclass(frozen=True)
class WorkflowContract:
    """A role-level contract imposed on one orchestration plan."""

    name: str
    required_roles: tuple[str, ...]
    role_dependencies: Mapping[str, tuple[str, ...]]
    acceptance_roles: tuple[str, ...]
    summary: str

    def planner_instructions(self) -> str:
        """A compact, visible version of the contract for planner/synth prompts."""
        lines = [
            "WORKFLOW CONTRACT (must be represented in the JSON plan):",
            f"- workflow: {self.name}",
            f"- required roles: {', '.join(self.required_roles)}",
        ]
        for role, upstream in self.role_dependencies.items():
            lines.append(f"- every {role} subtask directly depends on a {', '.join(upstream)} subtask")
        if self.acceptance_roles:
            lines.append(
                "- acceptance roles must be independent from implementation: "
                + ", ".join(self.acceptance_roles)
            )
        lines.append("- acceptance output must cite concrete repository or command evidence.")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "required_roles": list(self.required_roles),
            "role_dependencies": {
                role: list(upstream) for role, upstream in self.role_dependencies.items()
            },
            "acceptance_roles": list(self.acceptance_roles),
            "summary": self.summary,
        }


def workflow_for_goal(goal: str, *, write: bool) -> WorkflowContract:
    """Return the conservative default workflow for an orchestration mode.

    Read-only work uses investigation plus an independent review.  A write run
    is held to the full engineering chain: evidence -> implementation -> review
    and verification.  The goal is accepted for a future task-specific policy
    selector without making a brittle keyword decision today.
    """
    del goal
    if write:
        return WorkflowContract(
            name="engineering-change-v1",
            required_roles=("researcher", "implementer", "reviewer", "tester"),
            role_dependencies={
                "implementer": ("researcher",),
                "reviewer": ("implementer",),
                "tester": ("implementer",),
            },
            acceptance_roles=("reviewer", "tester"),
            summary=(
                "Research before implementation; a separate reviewer and tester "
                "accept the result after the implementation handoff."
            ),
        )
    return WorkflowContract(
        name="investigation-v1",
        required_roles=("researcher", "reviewer"),
        role_dependencies={"reviewer": ("researcher",)},
        acceptance_roles=("reviewer",),
        summary="A researcher supplies evidence and a separate reviewer checks the conclusion.",
    )


def inspect_handoffs(
    goal: str,
    results: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Parse available role outputs and run the existing artifact contract.

    Missing or prose-only artifacts are retained as ``parsed: false`` and the
    checker remains conservative.  This yields useful state/dashboard evidence
    while avoiding a false claim that an LLM-shaped paragraph passed a typed
    acceptance gate.
    """
    by_role: dict[str, str] = {}
    for result in results:
        role = str(result.get("assignee", ""))
        output = str(result.get("output", ""))
        if role and output and role not in by_role:
            by_role[role] = output
    plan = ArchitectPlan.from_text(by_role.get("researcher"), summary=goal)
    implementation = ImplementationReport.from_text(by_role.get("implementer"))
    review = ReviewReport.from_text(by_role.get("reviewer"))
    verification = VerificationReport.from_text(by_role.get("tester"))
    report = check_contract(plan, implementation, review, verification)
    return {
        "artifacts": {
            "plan": plan.model_dump(),
            "implementation": implementation.model_dump(),
            "review": review.model_dump(),
            "verification": verification.model_dump(),
        },
        "contract": report.model_dump(),
    }
