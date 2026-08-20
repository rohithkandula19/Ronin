"""Offline regression harness for the agent control plane.

Unlike model-quality evaluations, these cases exercise only deterministic policy:
repository-aware specialist routing, workflow selection, and governance bounds.
That makes them suitable for every pull request and provider-independent CI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .agent_catalog import RepositorySignals, build_catalog, select_profiles_for_repository
from .agent_governance import OrchestrationGovernance
from .agent_workflow import workflow_for_goal
from .orchestrate import core_agent_profiles


@dataclass(frozen=True)
class AgentControlCase:
    id: str
    task: str
    repository: RepositorySignals
    write: bool
    expected_profile: str | None = None
    expected_workflow: str = ""


@dataclass(frozen=True)
class AgentControlOutcome:
    case: AgentControlCase
    passed: bool
    detail: str


def default_cases() -> tuple[AgentControlCase, ...]:
    """Small fixture suite with stable, explainable expected decisions."""
    return (
        AgentControlCase(
            id="payment-security",
            task="audit payment security boundaries",
            repository=RepositorySignals(tags=("python", "payments", "security")),
            write=False,
            expected_profile="payments-security-auditor",
            expected_workflow="investigation-v1",
        ),
        AgentControlCase(
            id="python-change",
            task="add retry coverage for the client",
            repository=RepositorySignals(tags=("python", "testing")),
            write=True,
            expected_workflow="engineering-change-v1",
        ),
        AgentControlCase(
            id="release-path",
            task="prepare a release verification report",
            repository=RepositorySignals(tags=("release", "build", "devops")),
            write=True,
            expected_workflow="engineering-change-v1",
        ),
    )


def run_agent_control_eval(cases: Iterable[AgentControlCase] | None = None) -> list[AgentControlOutcome]:
    """Run all deterministic control-plane cases without filesystem or providers."""
    catalog = build_catalog(core_agent_profiles(), ".")
    core_keys = tuple(profile.key for profile in core_agent_profiles())
    governance = OrchestrationGovernance()
    outcomes: list[AgentControlOutcome] = []
    for case in cases or default_cases():
        selection = select_profiles_for_repository(
            case.task, catalog, core_keys=core_keys, repository=case.repository,
            max_agents=governance.max_agents,
        )
        workflow = workflow_for_goal(case.task, write=case.write)
        governance.validate_team(len(selection.profiles))
        profile_ok = case.expected_profile is None or any(
            profile.key == case.expected_profile for profile in selection.profiles
        )
        workflow_ok = workflow.name == case.expected_workflow
        passed = profile_ok and workflow_ok
        detail = (
            f"workflow={workflow.name}; team="
            + ",".join(profile.key for profile in selection.profiles)
        )
        outcomes.append(AgentControlOutcome(case, passed, detail))
    return outcomes


def render_agent_control_report(console, outcomes: Iterable[AgentControlOutcome]) -> bool:
    """Render the offline report and return whether every case passed."""
    from rich.table import Table

    outcomes = list(outcomes)
    table = Table(box=None, padding=(0, 2))
    table.add_column("", width=2)
    table.add_column("case", style="cyan")
    table.add_column("decision", overflow="fold")
    for outcome in outcomes:
        table.add_row("PASS" if outcome.passed else "FAIL", outcome.case.id, outcome.detail)
    console.print(table)
    passed = sum(outcome.passed for outcome in outcomes)
    console.print(f"agent control eval: {passed}/{len(outcomes)} passed")
    return passed == len(outcomes)
