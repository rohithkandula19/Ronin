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

from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel, Field

from . import roles as _roles

# architect designs → implementer edits → reviewer reviews → tester runs tests →
# verifier confirms the work actually meets the plan's acceptance criteria.
DEFAULT_ROLES: list[str] = ["architect", "implementer", "reviewer", "tester", "verifier"]

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


def _detail_for(stage: PipelineStage) -> str:
    """The trailing text for a stage line — its summary, else a default phase."""
    if stage.summary:
        return stage.summary
    if stage.status == BLOCKED:
        return "blocked"
    if stage.status == FAILED:
        return "failed"
    if stage.status == ACTIVE:
        return _PHASE.get(stage.role, "working")
    if stage.status == SKIPPED:
        return "skipped"
    return "waiting"


def _truncate(text: str, limit: int) -> str:
    text = text.replace("\n", " ").strip()
    if limit <= 1 or len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def stage_line(stage: PipelineStage, *, width: int = 80) -> str:
    """One Rich-markup line for a stage, with the detail truncated to ``width``.

    e.g. ``✓ architect — plan created``. Width-aware so it stays readable in a
    narrow terminal (the role + glyph always survive; the detail is trimmed)."""
    glyph = _GLYPH.get(stage.status, _GLYPH[PENDING])
    detail = _detail_for(stage)
    # visible budget: 2 indent + glyph(1) + space + role + " — "
    overhead = 2 + 1 + 1 + len(stage.role) + 3
    detail = _truncate(detail, max(6, width - overhead))
    body = f"{stage.role} [dim]— {detail}[/dim]"
    if stage.status == FAILED:
        body = f"[#f7768e]{stage.role}[/#f7768e] [dim]— {detail}[/dim]"
    elif stage.status == ACTIVE:
        body = f"[bold]{stage.role}[/bold] [dim]— {detail}[/dim]"
    return f"  {glyph} {body}"


def render_pipeline(console, state: PipelineState) -> None:
    """The compact live tracker — one line per stage with its current status."""
    width = getattr(console, "width", 80)
    console.print("  [bold]Pipeline[/bold]")
    for s in state.stages:
        console.print(stage_line(s, width=width), highlight=False)


def render_pipeline_plan(console, state: PipelineState) -> None:
    """--dry-run view: the planned sequence + what each stage may do, and whether
    the whole run is read-only or write-capable. Makes zero changes."""
    mode = "WRITE-CAPABLE (edits + commands gated)" if state.write_capable else "READ-ONLY (no edits, no commands)"
    console.print(f"  [bold]Pipeline plan[/bold] [dim](dry run — nothing runs)[/dim]")
    console.print(f"  [dim]task:[/dim] {state.task}")
    badge = f"[bold]{state.badge}[/bold] · " if state.badge else ""
    console.print(f"  [dim]brain:[/dim] {badge}{state.provider}/{state.model}")
    console.print(f"  [dim]mode:[/dim] {mode}")
    for i, role in enumerate(state.roles, 1):
        perm = stage_permission_label(role, write_capable=state.write_capable)
        console.print(f"    [cyan]{i}. {role:<12}[/cyan] [dim]{perm}[/dim]", highlight=False)


def render_pipeline_result(console, state: PipelineState) -> None:
    """The final per-stage summary + the overall recommendation."""
    console.print("  [bold]Pipeline result[/bold] "
                  f"[dim]· {state.outcome()}[/dim]")
    for s in state.stages:
        label = s.role[:1].upper() + s.role[1:]
        glyph = _GLYPH.get(s.status, _GLYPH[PENDING])
        detail = _detail_for(s)
        console.print(f"  {glyph} [bold]{label}:[/bold] [dim]{detail}[/dim]", highlight=False)
        if s.files_changed:
            console.print(f"      [dim]files: {', '.join(s.files_changed)}[/dim]", highlight=False)
    if state.final_recommendation:
        console.print(f"  [#2dd4bf]Final:[/#2dd4bf] {state.final_recommendation}", highlight=False)


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


# --- orchestration -----------------------------------------------------------

@dataclass
class StageOutcome:
    """What one stage produced. Returned by a stage runner."""
    success: bool
    summary: str
    files_changed: list[str] = field(default_factory=list)
    commands_requested: list[str] = field(default_factory=list)
    test_result: str = ""
    blocked: bool = False


# (config, role, prompt, *, read_only, root, console, max_iterations) -> StageOutcome
StageRunner = Callable[..., StageOutcome]

_STAGE_INSTRUCTION = {
    "architect": "Produce a concise, concrete implementation plan: the files/modules "
                 "to touch, the approach, and the ordered steps. Read-only — do not edit.",
    "implementer": "Carry out the plan with focused, idiomatic edits.",
    "reviewer": "Review the changes/plan so far for correctness, risks, security, and "
                "missing tests. Report findings (file·line · severity · fix). Read-only.",
    "tester": "Identify this project's tests for the change and verify them. Report real "
              "results — never claim green without a passing run.",
    "researcher": "Explore the relevant code read-only and report findings with file·line.",
    "debugger": "Find the root cause of the failure before any fix; explain the actual cause.",
}


def _summarize(text: str, limit: int = 240) -> str:
    text = (text or "").strip()
    if not text:
        return "(no output)"
    para = text.split("\n\n", 1)[0].replace("\n", " ").strip()
    return para if len(para) <= limit else para[: limit - 1] + "…"


def _stage_prompt(task: str, role: str, prior: list[tuple[str, str]],
                  *, write_capable: bool) -> str:
    lines = [f"Original task: {task}", ""]
    if prior:
        lines.append("Context from prior pipeline stages:")
        lines += [f"- {r}: {s}" for r, s in prior]
        lines.append("")
    lines.append(_STAGE_INSTRUCTION.get(role, "Do your part of the task for your role."))
    if role in ("implementer", "tester", "debugger") and not write_capable:
        lines.append("This is a READ-ONLY proposal run (no --write): describe exactly what "
                     "you would change/run, but make no edits and run no commands.")
    return "\n".join(lines)


def _default_stage_runner(config, role, prompt, *, read_only, root, console,
                          max_iterations) -> StageOutcome:
    from .code_mode import run_code_agent
    undo: list = []
    result = run_code_agent(
        config, prompt, root=root, console=console, yolo=False,
        read_only=read_only, role=role, undo_stack=undo, max_iterations=max_iterations,
    )
    files = sorted({e[0] for e in undo if isinstance(e, (list, tuple)) and e})
    return StageOutcome(
        success=bool(result.success) and not result.blocked,
        summary=_summarize(result.output),
        files_changed=files,
        blocked=bool(result.blocked),
    )


def _final_recommendation(state: PipelineState) -> str:
    out = state.outcome()
    done = sum(1 for s in state.stages if s.status == COMPLETED)
    total = len(state.stages)
    if out == "completed":
        return f"all {total} stages completed."
    stopper = next((s for s in state.stages if s.status in _STOP_STATUSES), None)
    if stopper is not None:
        return (f"stopped at {stopper.role} ({stopper.status}) after {done}/{total} "
                f"stages — resolve it and re-run.")
    return f"{done}/{total} stages completed."


def run_pipeline(
    config,
    task: str,
    role_list: list[str],
    *,
    write: bool = False,
    free: bool = False,
    offline: bool = False,
    dry_run: bool = False,
    root="." ,
    console=None,
    max_iterations: int = 25,
    stage_runner: StageRunner | None = None,
) -> PipelineState:
    """Run the roles in sequence, handing each stage's summary to the next.

    Sequential and single-agent-per-stage — NOT parallel, NOT autonomous. Each
    stage is a gated ``run_code_agent`` run wearing its role; read-only roles
    (and the whole pipeline without ``--write``) are enforced. A blocked/failed
    stage halts the run (remaining stages are marked skipped). ``dry_run`` runs
    nothing. Inject ``stage_runner`` to test the orchestration without a model.
    """
    from .offline import apply_free, apply_offline
    from .status import cost_badge

    cfg = config
    if offline:
        cfg = apply_offline(cfg.model_copy(update={"offline": True}))
    elif free:
        cfg = apply_free(cfg)
    badge = "LOCAL" if offline else cost_badge(cfg)[0]
    # Reflects what a real run WOULD do; dry-run safety comes from the early
    # return below (no stage ever runs), not from forcing this False.
    write_capable = bool(write)

    state = plan_pipeline(
        task, role_list, write_capable=write_capable, free=free, offline=offline,
        provider=cfg.provider, model=cfg.resolved_model(), badge=badge, dry_run=dry_run,
    )

    if dry_run:
        if console is not None:
            render_pipeline_plan(console, state)
        return state

    runner = stage_runner or _default_stage_runner
    prior: list[tuple[str, str]] = []
    for i, role in enumerate(role_list):
        stage = state.stages[i]
        stage.status = ACTIVE
        if console is not None:
            console.print(f"  [yellow]▶[/yellow] [bold]{role}[/bold] "
                          f"[dim]— {_PHASE.get(role, 'working')}[/dim]", highlight=False)
        ro = stage_read_only(role, write_capable=write_capable)
        prompt = _stage_prompt(task, role, prior, write_capable=write_capable)
        try:
            outcome = runner(cfg, role, prompt, read_only=ro, root=root,
                             console=console, max_iterations=max_iterations)
        except Exception as exc:  # noqa: BLE001 — one stage's crash shouldn't nuke the report
            stage.status = FAILED
            stage.summary = f"stage error: {exc}"
            break
        stage.summary = outcome.summary
        stage.files_changed = list(outcome.files_changed)
        stage.commands_requested = list(outcome.commands_requested)
        stage.test_result = outcome.test_result
        if outcome.blocked:
            stage.status = BLOCKED
            break  # stop — do not silently continue past a blocked stage
        if not outcome.success:
            stage.status = FAILED
            break
        stage.status = COMPLETED
        prior.append((role, outcome.summary))

    if state.stopped:  # mark the un-run tail as skipped
        for s in state.stages:
            if s.status == PENDING:
                s.status = SKIPPED

    state.final_recommendation = _final_recommendation(state)
    return state
