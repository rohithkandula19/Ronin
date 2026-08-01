"""Read-only project telemetry for governed agent runs.

The control plane already persists one state document for every run. This module
turns those documents into a compact dashboard without a server, database, or
new telemetry egress. Provider status is derived solely from observed subtask
outcomes; a provider with no successful work is never labelled healthy.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentRunSummary:
    id: str
    created: str
    updated: str
    status: str
    goal: str
    agents: int
    subtasks: int
    isolation: str


def list_agent_runs(root: Path | str, *, limit: int = 20) -> list[AgentRunSummary]:
    """Return bounded project-local run summaries, newest first."""
    directory = Path(root).resolve() / ".ronin" / "agent-runs"
    if not directory.is_dir():
        return []
    rows: list[AgentRunSummary] = []
    for path in directory.glob("agent-*.json"):
        state = _read_state(path)
        if state is None:
            continue
        selection = state.get("selection") if isinstance(state.get("selection"), dict) else {}
        profiles = selection.get("profiles") if isinstance(selection.get("profiles"), list) else []
        subtasks = state.get("subtasks") if isinstance(state.get("subtasks"), dict) else {}
        isolation = state.get("isolation") if isinstance(state.get("isolation"), dict) else {}
        rows.append(AgentRunSummary(
            id=str(state.get("id", path.stem)),
            created=str(state.get("created", "")),
            updated=str(state.get("updated", "")),
            status=str(state.get("status", "unknown")),
            goal=str(state.get("goal", "")),
            agents=len(profiles),
            subtasks=len(subtasks),
            isolation=str(isolation.get("mode", "project-root-read-only")),
        ))
    rows.sort(key=lambda row: (row.updated, row.id), reverse=True)
    return rows[:max(1, limit)]


def provider_observations(root: Path | str) -> dict[str, dict[str, Any]]:
    """Aggregate actual provider outcomes from saved agent states.

    ``attempts`` and ``failures`` are accumulated across runs. ``status`` is
    healthy only when at least one observed attempt succeeded and none failed;
    mixed evidence remains degraded, and a provider never appears without an
    observed subtask result.
    """
    directory = Path(root).resolve() / ".ronin" / "agent-runs"
    observed: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return observed
    for path in directory.glob("agent-*.json"):
        state = _read_state(path)
        if state is None or not isinstance(state.get("provider_health"), dict):
            continue
        updated = str(state.get("updated", ""))
        for provider, raw in state["provider_health"].items():
            if not isinstance(provider, str) or not isinstance(raw, dict):
                continue
            attempts = _non_negative_int(raw.get("attempts"))
            failures = min(_non_negative_int(raw.get("failures")), attempts)
            if not attempts:
                continue
            entry = observed.setdefault(provider, {
                "attempts": 0, "failures": 0, "successful_attempts": 0,
                "roles": set(), "last_error": None, "last_observed": "",
            })
            entry["attempts"] += attempts
            entry["failures"] += failures
            entry["successful_attempts"] += attempts - failures
            entry["roles"].update(str(role) for role in raw.get("roles", []) if isinstance(role, str))
            if raw.get("last_error"):
                entry["last_error"] = str(raw["last_error"])
            entry["last_observed"] = max(str(entry["last_observed"]), updated)
    for entry in observed.values():
        entry["roles"] = sorted(entry["roles"])
        entry["status"] = "healthy" if entry["successful_attempts"] and not entry["failures"] else "degraded"
    return observed


def dashboard_counts(root: Path | str) -> dict[str, int]:
    """Count saved run states by status for concise terminal dashboards."""
    return dict(Counter(run.status for run in list_agent_runs(root, limit=10_000)))


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
