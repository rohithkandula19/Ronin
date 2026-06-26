"""Role-handoff pipeline — run the Wave 3 roles in sequence with gated handoffs.

This is **sequential, single-agent-per-stage** orchestration, not parallel and
not autonomous: each stage is one ``run_code_agent`` run wearing a role, and its
summary is handed to the next stage. Every safety property of a normal turn
holds — read-only roles are enforced, writes/commands still hit the approval
gate, and ``--dry-run`` runs nothing at all.

Roles come from :mod:`ronin_cli.roles` (the single source of truth); this module
only sequences them and tracks state. The state model and renderers are pure and
fully testable; only :func:`run_pipeline` touches the agent.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from . import roles as _roles

# architect designs → implementer proposes/edits → reviewer reviews → tester verifies
DEFAULT_ROLES: list[str] = ["architect", "implementer", "reviewer", "tester"]

# stage status vocabulary (mirrors the plan tracker)
PENDING, ACTIVE, COMPLETED, BLOCKED, FAILED, SKIPPED = (
    "pending", "active", "completed", "blocked", "failed", "skipped")
_STOP_STATUSES = {BLOCKED, FAILED}

_GLYPH = {
    COMPLETED: "[green]✓[/green]",
    ACTIVE: "[yellow]▶[/yellow]",
    PENDING: "[dim]☐[/dim]",
    BLOCKED: "[#e0af68]⊘[/#e0af68]",
    FAILED: "[#f7768e]✗[/#f7768e]",
    SKIPPED: "[dim]⊝[/dim]",
}

# default "phase" line shown for a stage before it has a real summary
_PHASE = {
    "architect": "design the approach (read-only)",
    "implementer": "propose changes",
    "reviewer": "review the diff (read-only)",
    "tester": "verify with tests",
    "researcher": "explore (read-only)",
    "debugger": "find the root cause",
}


def parse_roles(spec: str | None) -> list[str]:
    """Parse a comma-separated role list into canonical keys.

    Returns :data:`DEFAULT_ROLES` for an empty/None spec. Raises ``ValueError``
    listing any unknown roles (so the CLI can show the valid set)."""
    if not spec or not spec.strip():
        return list(DEFAULT_ROLES)
    out, bad = [], []
    for part in spec.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name in _roles.ROLES:
            out.append(name)
        else:
            bad.append(name)
    if bad:
        raise ValueError(f"unknown role(s): {', '.join(bad)}; valid: {', '.join(_roles.ROLES)}")
    if not out:
        raise ValueError("no roles given")
    return out


def stage_read_only(role: str, *, write_capable: bool) -> bool:
    """True if this stage must run with read-only tools.

    Read-only roles (researcher / reviewer / architect) are always read-only.
    Doer roles (implementer / tester / debugger) are read-only unless the
    pipeline is write-capable (``--write``)."""
    return _roles.role_is_read_only(role) or not write_capable


def stage_permission_label(role: str, *, write_capable: bool) -> str:
    """Human label of what a stage may do — for --dry-run and the renderer."""
    if _roles.role_is_read_only(role):
        return "read-only"
    if not write_capable:
        return "read-only (proposal)"
    if role == "tester":
        return "runs tests (gated)"
    return "edits + commands (gated)"


class PipelineStage(BaseModel):
    role: str
    status: str = PENDING
    summary: str = ""
    files_changed: list[str] = Field(default_factory=list)
    commands_requested: list[str] = Field(default_factory=list)
    test_result: str = ""


class PipelineState(BaseModel):
    """The full, serializable state of a pipeline run."""
    task: str
    roles: list[str]
    write_capable: bool = False
    free: bool = False
    offline: bool = False
    provider: str = ""
    model: str = ""
    badge: str = ""              # FREE / PAID / LOCAL / UNKNOWN
    dry_run: bool = False
    stages: list[PipelineStage] = Field(default_factory=list)
    final_recommendation: str = ""

    def stage(self, role: str) -> PipelineStage | None:
        return next((s for s in self.stages if s.role == role), None)

    @property
    def stopped(self) -> bool:
        """True if any stage blocked or failed (the pipeline halts there)."""
        return any(s.status in _STOP_STATUSES for s in self.stages)

    def outcome(self) -> str:
        """One word: 'completed' / 'failed' / 'blocked' / 'planned' / 'partial'."""
        if self.dry_run:
            return "planned"
        statuses = [s.status for s in self.stages]
        if any(s == FAILED for s in statuses):
            return "failed"
        if any(s == BLOCKED for s in statuses):
            return "blocked"
        if statuses and all(s == COMPLETED for s in statuses):
            return "completed"
        return "partial"


def plan_pipeline(
    task: str,
    role_list: list[str],
    *,
    write_capable: bool = False,
    free: bool = False,
    offline: bool = False,
    provider: str = "",
    model: str = "",
    badge: str = "",
    dry_run: bool = False,
) -> PipelineState:
    """Build a fresh (all-pending) :class:`PipelineState`. Pure."""
    return PipelineState(
        task=task, roles=list(role_list), write_capable=write_capable, free=free,
        offline=offline, provider=provider, model=model, badge=badge, dry_run=dry_run,
        stages=[PipelineStage(role=r) for r in role_list],
    )
