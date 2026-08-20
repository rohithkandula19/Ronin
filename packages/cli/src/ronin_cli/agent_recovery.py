"""Safe recovery context for interrupted orchestrations.

Recovery never pretends a stopped run finished. It starts a new, linked run with
the original goal, selected profiles, and a compact status-only handoff. The
planner may recheck completed work, but it is told exactly what the predecessor
recorded and which subtasks were unfinished or failed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_catalog import AgentProfile
from .agent_state import load_agent_task_state


@dataclass(frozen=True)
class RecoveryContext:
    run_id: str
    goal: str
    profile_keys: tuple[str, ...]
    roster_spec: str | None
    agent_manifest: str | None
    max_agents: int
    write: bool
    completed: tuple[str, ...]
    failed: tuple[str, ...]
    unfinished: tuple[str, ...]

    def planner_context(self) -> str:
        """A bounded handoff for the replacement planner, with no raw outputs."""
        return (
            "This is a recovery run linked to prior orchestration "
            f"{self.run_id}. Do not claim prior work succeeded without checking it. "
            f"Completed subtasks: {_items(self.completed)}. "
            f"Failed subtasks: {_items(self.failed)}. "
            f"Unfinished subtasks: {_items(self.unfinished)}. "
            "Replan the goal from current repository evidence, preserving valid work and "
            "prioritising failed or unfinished acceptance work."
        )


def load_recovery_context(root: Path | str, run_id: str) -> RecoveryContext:
    """Read a non-terminal task state and turn it into a replacement-run handoff."""
    state = load_agent_task_state(root, run_id)
    if state is None:
        raise ValueError(f"no saved agent run named {run_id!r}")
    status = str(state.get("status", "unknown"))
    if status == "completed":
        raise ValueError("completed runs do not need recovery")
    goal = state.get("goal")
    selection = state.get("selection") if isinstance(state.get("selection"), dict) else {}
    profiles = selection.get("profiles") if isinstance(selection.get("profiles"), list) else []
    if not isinstance(goal, str) or not goal.strip() or not all(isinstance(key, str) and key for key in profiles):
        raise ValueError("saved agent run has no recoverable goal and profile selection")
    execution = state.get("execution") if isinstance(state.get("execution"), dict) else {}
    roster = execution.get("roster_spec") if isinstance(execution.get("roster_spec"), str) else None
    manifest = execution.get("agent_manifest") if isinstance(execution.get("agent_manifest"), str) else None
    try:
        max_agents = max(1, int(execution.get("max_agents", 8)))
    except (TypeError, ValueError):
        max_agents = 8
    read_only = bool(execution.get("read_only", True))
    plan = state.get("plan") if isinstance(state.get("plan"), list) else []
    subtasks = state.get("subtasks") if isinstance(state.get("subtasks"), dict) else {}
    planned_ids = [str(item.get("id")) for item in plan if isinstance(item, dict) and item.get("id")]
    completed: list[str] = []
    failed: list[str] = []
    unfinished: list[str] = []
    for subtask_id in planned_ids:
        raw = subtasks.get(subtask_id) if isinstance(subtasks.get(subtask_id), dict) else {}
        task_status = str(raw.get("status", "pending"))
        if task_status == "completed" and raw.get("success"):
            completed.append(subtask_id)
        elif task_status == "failed":
            failed.append(subtask_id)
        else:
            unfinished.append(subtask_id)
    # A provider/setup failure can occur before the planner writes a plan. The
    # replacement run remains valid; it simply receives an empty task handoff.
    return RecoveryContext(
        run_id=str(state.get("id", run_id)),
        goal=goal.strip(),
        profile_keys=tuple(profiles),
        roster_spec=roster,
        agent_manifest=manifest,
        max_agents=max_agents,
        write=not read_only,
        completed=tuple(completed),
        failed=tuple(failed),
        unfinished=tuple(unfinished),
    )


def recover_profiles(root: Path | str, context: RecoveryContext, catalog: tuple[AgentProfile, ...]) -> tuple[AgentProfile, ...]:
    """Resolve the original profile keys, refusing a silent team substitution."""
    by_key = {profile.key: profile for profile in catalog}
    missing = [key for key in context.profile_keys if key not in by_key]
    if missing:
        raise ValueError("recovery profiles are no longer available: " + ", ".join(missing))
    return tuple(by_key[key] for key in context.profile_keys)


def list_recoverable_runs(root: Path | str, *, limit: int = 20) -> list[RecoveryContext]:
    """Return recoverable runs newest first without loading arbitrary paths."""
    directory = Path(root).resolve() / ".ronin" / "agent-runs"
    if not directory.is_dir():
        return []
    contexts: list[RecoveryContext] = []
    for path in sorted(directory.glob("agent-*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            context = load_recovery_context(root, path.stem)
        except (OSError, ValueError):
            continue
        contexts.append(context)
        if len(contexts) >= max(1, limit):
            break
    return contexts


def _items(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none recorded"
