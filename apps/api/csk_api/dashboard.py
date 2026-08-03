"""Read-only data layer for the web dashboard (``ronin ui``).

This module reads REAL stored data — it does not fabricate anything. Each helper
surfaces data the rest of ronin actually wrote to disk:

- recent runs        -> ``ronin_cli.run_store`` (orchestrated runs) +
                        ``ronin_cli.sessions`` (chat sessions, ``.ronin/sessions``)
- a run's detail     -> the full run record (planner -> sub-agent tree, plan,
                        per-subtask results, faithfulness)
- memory entries     -> ``ronin_cli.memory_store`` (``~/.ronin/memory.json``)
- skills             -> ``ronin_cli.muscle_memory`` (``.ronin/commands/*.md``)
- fleet plans        -> ``ronin_cli.agent_fleet`` (``.ronin/fleet-plans``)
- missions           -> ``ronin_cli.mission_store`` (``.ronin/missions``)
- candidate workspaces -> ``ronin_cli.candidate_workspace`` (local metadata only)
- patch proposals    -> ``ronin_cli.agent_proposals`` (``.ronin/agent-proposals``)

Everything honours ``RONIN_HOME``/the current working directory exactly as the
CLI does, so a test can point a temp ``.ronin`` home at these helpers and get the
data it wrote back. No network, no keys, no DB. If there is no data yet the
helpers return empty lists and the UI shows an honest empty state.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def recent_runs(limit: int = 50) -> list[dict[str, Any]]:
    """Recent runs, most recent first: archived orchestrations plus chat
    sessions. Each is a small summary; the detail view loads the full record."""
    runs: list[dict[str, Any]] = []

    try:
        from ronin_cli.run_store import list_runs

        for r in list_runs(limit=limit):
            runs.append({
                "id": r.get("id", ""),
                "kind": r.get("kind", "orchestrate"),
                "title": r.get("goal", "") or "(no goal)",
                "created": r.get("created", ""),
                "success": bool(r.get("success", False)),
                "subtasks": int(r.get("n_subtasks", 0)),
                "planner": r.get("planner", ""),
            })
    except Exception:  # noqa: BLE001 — a missing store is just "no data"
        pass

    try:
        from ronin_cli.sessions import list_sessions

        for s in list_sessions():
            runs.append({
                "id": s.get("id", ""),
                "kind": "session",
                "title": s.get("title", "") or "(no title)",
                "created": s.get("updated", ""),
                "success": True,
                "subtasks": 0,
                "turns": int(s.get("turns", 0)),
                "root": s.get("root", ""),
            })
    except Exception:  # noqa: BLE001
        pass

    runs.sort(key=lambda r: (r.get("created", ""), r.get("id", "")), reverse=True)
    return runs[: max(1, limit)]


def run_detail(run_id: str) -> dict[str, Any] | None:
    """Full detail for a run id. Tries the orchestration run store first (planner
    -> sub-agent tree + faithfulness), then falls back to a chat session
    transcript. Returns ``None`` if neither has it."""
    try:
        from ronin_cli.run_store import load_run

        rec = load_run(run_id)
        if rec is not None:
            return _orchestration_detail(rec)
    except Exception:  # noqa: BLE001
        pass

    try:
        from ronin_cli.sessions import list_sessions, load_session

        transcript = load_session(run_id)
        if transcript:
            meta = next(
                (s for s in list_sessions() if s.get("id") == run_id), {}
            )
            return {
                "id": run_id,
                "kind": "session",
                "title": meta.get("title", "") or "(no title)",
                "created": meta.get("updated", ""),
                "success": True,
                "transcript": transcript,
                "subagents": [],
                "subtasks": [],
                "results": [],
                "faithfulness": None,
            }
    except Exception:  # noqa: BLE001
        pass

    return None


def _orchestration_detail(rec: dict[str, Any]) -> dict[str, Any]:
    """Shape a stored orchestration record into the detail the UI renders.

    Builds the planner -> sub-agent tree: the planner node carries its
    provider/model label and each sub-agent branch carries its role + provider
    plus the subtasks the plan assigned to it (with their per-subtask results and
    faithfulness). All fields are read straight from the stored record."""
    subagents = rec.get("subagents") or []
    subtasks = rec.get("subtasks") or []
    results = rec.get("results") or []

    results_by_id = {
        r.get("subtask_id"): r for r in results if isinstance(r, dict)
    }

    # Group subtasks under the sub-agent role that was assigned to them.
    by_role: dict[str, list[dict[str, Any]]] = {}
    for st in subtasks:
        if not isinstance(st, dict):
            continue
        role = st.get("assignee", "")
        res = results_by_id.get(st.get("id"), {})
        by_role.setdefault(role, []).append({
            "id": st.get("id", ""),
            "description": st.get("description", ""),
            "depends_on": st.get("depends_on", []) or [],
            "success": bool(res.get("success", False)),
            "output": res.get("output", "") or "",
            "error": res.get("error"),
            "iterations": int(res.get("iterations", 0) or 0),
            "faithfulness": res.get("faithfulness"),
        })

    tree_subagents: list[dict[str, Any]] = []
    seen_roles = set()
    for sa in subagents:
        if not isinstance(sa, dict):
            continue
        role = sa.get("role", "")
        seen_roles.add(role)
        tree_subagents.append({
            "role": role,
            "provider": sa.get("provider", ""),
            "used": bool(sa.get("used", role in by_role)),
            "subtasks": by_role.get(role, []),
        })
    # A plan may assign a role that was not in the stored roster (custom plan).
    for role, items in by_role.items():
        if role not in seen_roles:
            tree_subagents.append({
                "role": role,
                "provider": "",
                "used": True,
                "subtasks": items,
            })

    return {
        "id": rec.get("id", ""),
        "kind": rec.get("kind", "orchestrate"),
        "title": rec.get("goal", "") or "(no goal)",
        "created": rec.get("created", ""),
        "success": bool(rec.get("success", False)),
        "error": rec.get("error"),
        "planner": rec.get("planner", ""),
        "output": rec.get("output", "") or "",
        "faithfulness": rec.get("faithfulness"),
        "subagents": tree_subagents,
        "subtasks": subtasks,
        "results": results,
        "transcript": [],
    }


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

def memory_entries(limit: int = 200) -> list[dict[str, Any]]:
    """Durable user facts ronin remembers (``~/.ronin/memory.json``), most recent
    first. Empty list when nothing has been remembered."""
    try:
        from ronin_cli.memory_store import load_memories
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for m in load_memories():
        if isinstance(m, dict) and m.get("text"):
            out.append({"text": str(m["text"]), "ts": m.get("ts")})
    out.reverse()
    return out[: max(1, limit)]


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

def skill_entries(root: str | Path = ".") -> list[dict[str, Any]]:
    """Crystallized repo-local skills (``.ronin/commands/*.md``) for ``root``,
    each ``{name, body}`` (body truncated). Empty list when none exist."""
    try:
        from ronin_cli.muscle_memory import list_skills, skills_dir
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    d = skills_dir(root)
    for name in list_skills(root):
        body = ""
        try:
            body = (d / f"{name}.md").read_text(encoding="utf-8")
        except OSError:
            body = ""
        out.append({"name": name, "body": body[:4000]})
    return out


# ---------------------------------------------------------------------------
# Agent operations
# ---------------------------------------------------------------------------

def fleet_plan_entries(root: str | Path = ".", limit: int = 50) -> list[dict[str, Any]]:
    """Inspectable summaries of saved agent-fleet plans for ``root``.

    Planning a fleet does not start workers. The dashboard deliberately exposes
    only stored plan metadata and wave scheduling so viewing it remains read-only.
    """
    try:
        from ronin_cli.agent_fleet import FleetPlanStore
    except Exception:  # noqa: BLE001
        return []

    try:
        plans = FleetPlanStore(root).list(limit=limit)
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "id": plan.id,
            "created": plan.created,
            "goal": plan.goal,
            "write": plan.write,
            "catalog_size": plan.catalog_size,
            "member_count": len(plan.members),
            "max_parallel": plan.max_parallel,
            "waves": [
                {
                    "number": wave.number,
                    "phase": wave.phase,
                    "parallelism": wave.parallelism,
                    "waits_for": list(wave.waits_for),
                }
                for wave in plan.waves
            ],
        }
        for plan in plans
    ]


def fleet_run_entries(root: str | Path = ".", limit: int = 50) -> list[dict[str, Any]]:
    """Inspectable execution state for locally claimed fleet waves.

    The API intentionally returns status and identifiers only. Executing,
    retrying, recovering, staging, and merging remain explicit CLI actions.
    """
    try:
        from ronin_cli.agent_fleet_runs import FleetRunStore
    except Exception:  # noqa: BLE001
        return []

    try:
        runs = FleetRunStore(root).list(limit=limit)
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "id": run.id,
            "plan_id": run.plan_id,
            "goal": run.goal,
            "write": run.write,
            "status": run.status,
            "created": run.created,
            "updated": run.updated,
            "error": run.error,
            "waves": [
                {
                    "number": wave.number,
                    "phase": wave.phase,
                    "status": wave.status,
                    "attempts": wave.attempts,
                    "parallelism": len(wave.profile_keys),
                    "agent_run_id": wave.agent_run_id,
                    "proposal_run_id": wave.proposal_run_id,
                    "error": wave.error,
                }
                for wave in run.waves
            ],
        }
        for run in runs
    ]


def mission_entries(root: str | Path = ".", limit: int = 50) -> list[dict[str, Any]]:
    """Status-only mission records for the local Mission Control panel.

    Issue bodies and artifact content are deliberately excluded: this page is an
    operational overview, while the typed record remains in the project-local
    mission store. Audit validation is included so an operator can see whether
    the durable snapshot still agrees with its append-only evidence.
    """
    try:
        from ronin_cli.mission_store import MissionStore
    except Exception:  # noqa: BLE001
        return []

    try:
        store = MissionStore(root)
        missions = store.list(limit=limit)
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "id": mission.id,
            "title": mission.spec.title,
            "source": mission.spec.source,
            "source_id": mission.spec.source_id,
            "stage": mission.stage.value,
            "created": mission.created,
            "updated": mission.updated,
            "candidate_workspace_id": mission.candidate_workspace_id,
            "event_count": mission.event_count,
            "audit_valid": store.verify_audit(mission.id).valid,
            "plan_recorded": mission.artifacts.plan is not None,
            "test_verdict": mission.artifacts.test_report.verdict if mission.artifacts.test_report else "unknown",
            "review_verdict": mission.artifacts.review_report.verdict if mission.artifacts.review_report else "unknown",
            "security_verdict": mission.artifacts.security_scan.verdict if mission.artifacts.security_scan else "unknown",
        }
        for mission in missions
    ]


def candidate_workspace_entries(root: str | Path = ".", limit: int = 50) -> list[dict[str, Any]]:
    """Safe candidate lifecycle metadata; checkout paths and content stay private."""
    try:
        from ronin_cli.candidate_workspace import CandidateWorkspaceService
    except Exception:  # noqa: BLE001
        return []

    try:
        candidates = CandidateWorkspaceService(root).list(limit=limit)
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "id": candidate.id,
            "mission_id": candidate.mission_id,
            "base_revision": candidate.base_revision,
            "image": candidate.image,
            "status": candidate.status,
            "created": candidate.created,
            "updated": candidate.updated,
            "destroyed": candidate.destroyed,
        }
        for candidate in candidates
    ]


def proposal_entries(root: str | Path = ".", limit: int = 50) -> list[dict[str, Any]]:
    """Inspectable summaries of retained isolated-worktree patch proposals."""
    try:
        from ronin_cli.agent_proposals import AgentProposalStore
    except Exception:  # noqa: BLE001
        return []

    try:
        proposals = AgentProposalStore(root).list()
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "run_id": proposal.run_id,
            "created": proposal.created,
            "status": proposal.status,
            "base_revision": proposal.base_revision,
            "roles": [patch.role for patch in proposal.patches],
            "patch_count": len(proposal.patches),
            "failed_subtasks": proposal.failed_subtasks,
        }
        for proposal in proposals[: max(1, limit)]
    ]
