"""Host-owned operating limits for an orchestrated agent team.

The catalog answers *who* may be invited.  Governance answers how much of the
machine and provider surface that team is allowed to consume.  It is pure data
so settings can be saved with an agent run and evaluated without a provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OrchestrationGovernance:
    """Bounded concurrency, per-agent budgets, and acceptance safeguards."""

    max_agents: int = 8
    max_parallel: int = 4
    max_iterations_per_agent: int = 12
    max_total_subtask_iterations: int = 96
    max_wall_time_seconds_per_agent: float | None = 300.0
    max_tokens_per_agent: int | None = None
    require_independent_acceptance: bool = True
    provider_failure_mode: str = "record"

    def __post_init__(self) -> None:
        if not 1 <= self.max_agents <= 32:
            raise ValueError("max_agents must be between 1 and 32")
        if not 1 <= self.max_parallel <= self.max_agents:
            raise ValueError("max_parallel must be between 1 and max_agents")
        if self.max_iterations_per_agent < 1:
            raise ValueError("max_iterations_per_agent must be positive")
        if self.max_total_subtask_iterations < self.max_iterations_per_agent:
            raise ValueError("max_total_subtask_iterations must cover one agent")
        if self.max_wall_time_seconds_per_agent is not None and self.max_wall_time_seconds_per_agent <= 0:
            raise ValueError("max_wall_time_seconds_per_agent must be positive")
        if self.max_tokens_per_agent is not None and self.max_tokens_per_agent < 1:
            raise ValueError("max_tokens_per_agent must be positive")
        if self.provider_failure_mode not in {"record", "fail_closed"}:
            raise ValueError("provider_failure_mode must be 'record' or 'fail_closed'")

    def validate_team(self, team_size: int) -> None:
        if team_size < 1:
            raise ValueError("orchestration needs at least one agent")
        if team_size > self.max_agents:
            raise ValueError(
                f"selected {team_size} agents, above governance max_agents={self.max_agents}"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_agents": self.max_agents,
            "max_parallel": self.max_parallel,
            "max_iterations_per_agent": self.max_iterations_per_agent,
            "max_total_subtask_iterations": self.max_total_subtask_iterations,
            "max_wall_time_seconds_per_agent": self.max_wall_time_seconds_per_agent,
            "max_tokens_per_agent": self.max_tokens_per_agent,
            "require_independent_acceptance": self.require_independent_acceptance,
            "provider_failure_mode": self.provider_failure_mode,
        }


def provider_health(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Summarize observed model health without guessing availability.

    A provider is only called healthy after a real successful subtask. Results
    without a provider label degrade to their role name, which keeps manually
    constructed or older records readable.
    """
    health: dict[str, dict[str, Any]] = {}
    for result in results:
        role = str(result.get("assignee", "unknown"))
        provider = str(result.get("provider") or role)
        entry = health.setdefault(provider, {
            "attempts": 0, "failures": 0, "last_error": None, "roles": [],
        })
        entry["attempts"] += 1
        if role not in entry["roles"]:
            entry["roles"].append(role)
        if not result.get("success", False):
            entry["failures"] += 1
            entry["last_error"] = result.get("error")
    for entry in health.values():
        entry["status"] = "healthy" if entry["failures"] == 0 else "degraded"
    return health
