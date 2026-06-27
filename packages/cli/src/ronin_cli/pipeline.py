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
    artifact: dict = Field(default_factory=dict)  # serialized handoff artifact


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
    root: str = ""               # repo path the run targeted (for resume sanity checks)
    stages: list[PipelineStage] = Field(default_factory=list)
    verdict: str = ""            # verifier/tester stage verdict: passed/failed/blocked/unknown
    independent_verify: dict = Field(default_factory=dict)  # serialized VerifyRun
    verify_source: str = ""      # provided / detected / not_found / disabled
    contract: dict = Field(default_factory=dict)            # serialized ContractCheckReport
    semantic: dict = Field(default_factory=dict)            # serialized SemanticContractReport
    diff_evidence: dict = Field(default_factory=dict)       # serialized DiffEvidence (real diff)
    suites: list[dict] = Field(default_factory=list)        # serialized SuiteRun list
    git_snapshot: dict = Field(default_factory=dict)        # serialized GitSnapshot at run start
    final_verdict: str = ""      # combined Wave-6+ verdict (verifier + verify + contract + …)
    final_recommendation: str = ""

    def stage(self, role: str) -> PipelineStage | None:
        return next((s for s in self.stages if s.role == role), None)

    def architect_plan(self):
        """Reconstruct the ArchitectPlan artifact, or None."""
        from .pipeline_artifacts import ArchitectPlan
        s = self.stage("architect")
        return ArchitectPlan.model_validate(s.artifact) if s and s.artifact else None

    def verification(self):
        """The authoritative VerificationReport (verifier's, else tester's), or None."""
        from .pipeline_artifacts import VerificationReport
        for role in ("verifier", "tester"):
            s = self.stage(role)
            if s and s.artifact:
                return VerificationReport.model_validate(s.artifact)
        return None

    def implementation_report(self):
        from .pipeline_artifacts import ImplementationReport
        s = self.stage("implementer")
        return ImplementationReport.model_validate(s.artifact) if s and s.artifact else None

    def review_report(self):
        from .pipeline_artifacts import ReviewReport
        s = self.stage("reviewer")
        return ReviewReport.model_validate(s.artifact) if s and s.artifact else None

    def acceptance_summary(self) -> dict[str, list[str]]:
        """met / unmet / unknown / not_applicable buckets from the verification."""
        from .pipeline_artifacts import roll_up_criteria
        return roll_up_criteria(self.verification())

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


_VERDICT_HEX = {"passed": "#9ece6a", "failed": "#f7768e",
                "blocked": "#e0af68", "unknown": "#6b7089"}


def render_acceptance(console, state: PipelineState) -> None:
    """Acceptance-criteria rollup (met / unmet / unknown), if a verifier ran."""
    crit = state.acceptance_summary()
    if not any(crit.values()):
        return
    console.print("  [bold]Acceptance criteria[/bold]")
    for status, glyph, hexc in (("met", "✓", "#9ece6a"), ("unmet", "✗", "#f7768e"),
                                ("unknown", "?", "#e0af68"), ("not_applicable", "–", "#6b7089")):
        for text in crit[status]:
            console.print(f"    [{hexc}]{glyph}[/{hexc}] [dim]{text}[/dim]", highlight=False)


def render_pipeline_result(console, state: PipelineState) -> None:
    """The final per-stage summary, acceptance criteria, verdict + recommendation."""
    console.print("  [bold]Pipeline result[/bold] "
                  f"[dim]· {state.outcome()}[/dim]")
    for s in state.stages:
        label = s.role[:1].upper() + s.role[1:]
        glyph = _GLYPH.get(s.status, _GLYPH[PENDING])
        detail = _detail_for(s)
        console.print(f"  {glyph} [bold]{label}:[/bold] [dim]{detail}[/dim]", highlight=False)
        if s.files_changed:
            console.print(f"      [dim]files: {', '.join(s.files_changed)}[/dim]", highlight=False)
    render_suites(console, state)
    render_acceptance(console, state)
    render_final_verification(console, state)
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
    artifact: dict | None = None  # serialized structured artifact for the next stage


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
    "verifier": "READ-ONLY final check. Confirm the implementation meets the architect's "
                "acceptance criteria, that the claimed tests actually ran, and that changed "
                "files match the plan. Flag unverified claims. Verdict: passed/failed/blocked/"
                "unknown — never mark unknown as passed.",
    "researcher": "Explore the relevant code read-only and report findings with file·line.",
    "debugger": "Find the root cause of the failure before any fix; explain the actual cause.",
}

# Per-role JSON shape the stage is asked to emit (kept short on purpose).
_ARTIFACT_HINT = {
    "architect": '{"objective": "...", "constraints": [], "assumptions": [], '
                 '"files_to_inspect": [], "files_to_change": [], "implementation_steps": [], '
                 '"risks": [], "test_strategy": "...", '
                 '"acceptance_criteria": [{"id": "ac1", "text": "...", "status": "unknown"}]}',
    "implementer": '{"completed_steps": [], "files_changed": [], "diff_summary": "...", '
                   '"commands_requested": [], "commands_run": [], "unresolved_items": [], '
                   '"confidence": "low|medium|high"}',
    "reviewer": '{"findings": [{"text": "...", "severity": "info|low|medium|high|blocking", '
                '"affected_files": []}], "required_fixes": [], "safety_concerns": [], '
                '"architecture_concerns": [], "approval_recommendation": "approve|request_changes"}',
    "tester": '{"tests_discovered": [], "tests_run": [], "passed": 0, "failed": 0, '
              '"command_outputs_summary": "...", "final_verdict": "passed|failed|blocked|unknown"}',
    "verifier": '{"tests_run": [], "passed": 0, "failed": 0, "blocked_reason": "", '
                '"acceptance_criteria_status": [{"id": "ac1", "text": "...", '
                '"status": "met|unmet|unknown|not_applicable"}], '
                '"final_verdict": "passed|failed|blocked|unknown"}',
}


def _summarize(text: str, limit: int = 240) -> str:
    text = (text or "").strip()
    if not text:
        return "(no output)"
    para = text.split("\n\n", 1)[0].replace("\n", " ").strip()
    return para if len(para) <= limit else para[: limit - 1] + "…"


def _stage_prompt(task: str, role: str, prior_artifacts: list[dict],
                  *, write_capable: bool) -> str:
    import json as _json
    lines = [f"Original task: {task}", ""]
    if prior_artifacts:
        lines.append("Structured artifacts from prior pipeline stages (consume these):")
        for art in prior_artifacts:
            lines.append("```json")
            lines.append(_json.dumps(art, default=str)[:1800])
            lines.append("```")
        lines.append("")
    lines.append(_STAGE_INSTRUCTION.get(role, "Do your part of the task for your role."))
    if role in ("implementer", "tester", "debugger") and not write_capable:
        lines.append("This is a READ-ONLY proposal run (no --write): describe exactly what "
                     "you would change/run, but make no edits and run no commands.")
    hint = _ARTIFACT_HINT.get(role)
    if hint:
        lines.append("")
        lines.append("End your reply with a single ```json block matching this shape "
                     "(use explicit empty/unknown values for anything you can't determine):")
        lines.append(hint)
    return "\n".join(lines)


def _default_stage_runner(config, role, prompt, *, read_only, root, console,
                          max_iterations) -> StageOutcome:
    from .code_mode import run_code_agent
    from .pipeline_artifacts import (
        ImplementationReport,
        VerificationReport,
        artifact_for_role,
        finalize_verdict,
    )
    undo: list = []
    result = run_code_agent(
        config, prompt, root=root, console=console, yolo=False,
        read_only=read_only, role=role, undo_stack=undo, max_iterations=max_iterations,
    )
    files = sorted({e[0] for e in undo if isinstance(e, (list, tuple)) and e})

    # Build the role's structured artifact from the reply (lenient; explicit
    # unknowns on failure), then reconcile with real evidence.
    art = artifact_for_role(role).from_text(result.output, summary=_summarize(result.output))
    if isinstance(art, ImplementationReport) and not art.files_changed:
        art.files_changed = files  # ground-truth from the undo stack
    if isinstance(art, VerificationReport):
        art.final_verdict = finalize_verdict(art)  # conservative; never fakes "passed"

    return StageOutcome(
        success=bool(result.success) and not result.blocked,
        summary=_summarize(result.output),
        files_changed=files,
        blocked=bool(result.blocked),
        artifact=art.model_dump(),
    )


_VERDICT_MSG = {
    "passed": "verifier PASSED — acceptance criteria met.",
    "failed": "verifier FAILED — the work does not meet the task.",
    "blocked": "verifier BLOCKED — verification could not complete.",
    "unknown": "verifier UNKNOWN — could not confirm; treat as NOT done.",
}


def _final_recommendation(state: PipelineState) -> str:
    done = sum(1 for s in state.stages if s.status == COMPLETED)
    total = len(state.stages)
    stopper = next((s for s in state.stages if s.status in _STOP_STATUSES), None)
    if stopper is not None:
        return (f"stopped at {stopper.role} ({stopper.status}) after {done}/{total} "
                f"stages — resolve it and re-run.")
    verdict = state.final_verdict or state.verdict
    if verdict:
        crit = state.acceptance_summary()
        tail = ""
        if crit["unmet"]:
            tail += f" · {len(crit['unmet'])} criteria unmet"
        if crit["unknown"]:
            tail += f" · {len(crit['unknown'])} unverified"
        return _VERDICT_MSG.get(verdict, f"verdict: {verdict}") + tail
    if done == total and total:
        return f"all {total} stages completed."
    return f"{done}/{total} stages completed."


def compute_final_verdict(state: PipelineState) -> str:
    """The combined Wave-6 verdict: passed / failed / blocked / unknown (pure).

    Precedence (safety-first): a halted stage wins; then any hard fail (failed
    independent verify, failed contract, unmet acceptance criterion, failed
    verifier); then blocks (declined/blocked verify, blocked verifier). 'passed'
    requires real evidence — a passing independent verify, or (advisory mode, no
    verify command) a passing verifier — with the contract not failed."""
    from .pipeline_verify import VerifyRun
    if any(s.status == BLOCKED for s in state.stages):
        return "blocked"
    if any(s.status == FAILED for s in state.stages):
        return "failed"

    iv = VerifyRun.model_validate(state.independent_verify) if state.independent_verify else VerifyRun()
    iv_verdict = iv.verdict()
    contract_status = (state.contract or {}).get("final_contract_status", "unknown")
    has_unmet = bool(state.acceptance_summary()["unmet"])
    has_blockers = bool((state.contract or {}).get("blocking_issues"))
    verifier_v = state.verdict

    # Semantic check is advisory unless it reports a clear misalignment ('failed').
    semantic_status = (state.semantic or {}).get("final_semantic_status", "")
    if iv_verdict == "failed" or contract_status == "failed" or has_unmet \
            or has_blockers or verifier_v == "failed" or semantic_status == "failed":
        return "failed"
    if iv_verdict == "blocked" or verifier_v == "blocked":
        return "blocked"
    if iv_verdict == "passed" and contract_status != "failed":
        return "passed"  # real verification is sufficient even if the verifier was unsure
    if iv_verdict == "not_provided" and verifier_v == "passed" and contract_status != "failed":
        return "passed"  # advisory mode preserved (Wave 5 behavior)
    return "unknown"


def truth_table(state: PipelineState) -> dict[str, str]:
    """The compact final-verification summary as plain strings (pure/testable)."""
    from .pipeline_verify import VerifyRun
    ver = state.verification()
    iv = VerifyRun.model_validate(state.independent_verify) if state.independent_verify else VerifyRun()
    if (ver and ver.tests_run) or iv.ran:
        tests_run = "yes"
    elif ver is not None:
        tests_run = "no"
    else:
        tests_run = "unknown"
    crit = state.acceptance_summary()
    contract_status = (state.contract or {}).get("final_contract_status", "unknown")
    blockers = (state.contract or {}).get("blocking_issues") or []
    git_status = (state.git_snapshot or {}).get("dirty_state")
    git_cell = "unavailable" if not state.git_snapshot else ("changed" if git_status else "clean")
    semantic_cell = (state.semantic or {}).get("final_semantic_status", "disabled") \
        if state.semantic else "disabled"
    de = state.diff_evidence or {}
    if not de or not de.get("captured"):
        diff_cell = "disabled" if de.get("note") == "diff evidence disabled" else "missing"
    elif de.get("truncated"):
        diff_cell = "truncated"
    elif de.get("full_diff_available"):
        diff_cell = "available"
    else:
        diff_cell = "missing"
    n = len(state.suites)
    passed_n = sum(1 for s in state.suites if s.get("status") == "passed")
    failed_n = sum(1 for s in state.suites if s.get("status") == "failed")
    suites_cell = f"{n} total, {passed_n} passed, {failed_n} failed" if n else "none"
    return {
        "diff_evidence": diff_cell,
        "suites": suites_cell,
        "verify_command": state.verify_source or "not_found",
        "tests_run": tests_run,
        "verify_result": iv.verdict(),
        "git_snapshot": git_cell,
        "acceptance": f"{len(crit['met'])} met, {len(crit['unmet'])} unmet, "
                      f"{len(crit['unknown'])} unknown",
        "contract": contract_status,
        "semantic_contract": semantic_cell,
        "review_blockers": "present" if blockers else "none",
        "final_verdict": state.final_verdict or "unknown",
    }


_SUITE_GLYPH = {"passed": "[green]✓[/green]", "failed": "[#f7768e]✗[/#f7768e]",
                "blocked": "[#e0af68]⊘[/#e0af68]", "skipped": "[dim]–[/dim]",
                "unknown": "[dim]?[/dim]"}


def render_suites(console, state: PipelineState) -> None:
    """Compact per-suite table (name · command · status · exit · duration)."""
    if not state.suites:
        return
    console.print("  [bold]Verification suites[/bold]")
    for s in state.suites:
        glyph = _SUITE_GLYPH.get(s.get("status", "unknown"), "[dim]?[/dim]")
        ec = s.get("exit_code")
        meta = f"exit {ec}" if ec is not None else s.get("status", "")
        dur = f"{s.get('duration', 0):.2f}s"
        console.print(f"    {glyph} [bold]{s.get('name', '')}[/bold] "
                      f"[dim]{s.get('command', '')}[/dim] [#6b7089]· {meta} · {dur}[/#6b7089]",
                      highlight=False)


def render_final_verification(console, state: PipelineState) -> None:
    """The compact 'Final Verification' truth table."""
    t = truth_table(state)
    hexc = _VERDICT_HEX.get(t["final_verdict"], "#6b7089")
    console.print("  [bold]Final Verification[/bold]")
    console.print(f"    [dim]Diff evidence:[/dim]      {t['diff_evidence']}", highlight=False)
    console.print(f"    [dim]Suites run:[/dim]         {t['suites']}", highlight=False)
    console.print(f"    [dim]Verify command:[/dim]     {t['verify_command']}", highlight=False)
    console.print(f"    [dim]Verify result:[/dim]      {t['verify_result']}", highlight=False)
    console.print(f"    [dim]Tests run:[/dim]          {t['tests_run']}", highlight=False)
    console.print(f"    [dim]Git snapshot:[/dim]       {t['git_snapshot']}", highlight=False)
    console.print(f"    [dim]Acceptance criteria:[/dim] {t['acceptance']}", highlight=False)
    console.print(f"    [dim]Contract checks:[/dim]    {t['contract']}", highlight=False)
    console.print(f"    [dim]Semantic contract:[/dim]  {t['semantic_contract']}", highlight=False)
    console.print(f"    [dim]Review blockers:[/dim]    {t['review_blockers']}", highlight=False)
    console.print(f"    [dim]Final verdict:[/dim]      "
                  f"[{hexc}]{t['final_verdict'].upper()}[/{hexc}]", highlight=False)


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
    verify_cmd: str | None = None,
    verify_cmds: list[str] | None = None,
    verify_suites: list[str] | None = None,
    auto_verify_all: bool = False,
    verify_timeout: int = 600,
    independent_verify_enabled: bool = True,
    auto_verify_enabled: bool = True,
    semantic_enabled: bool = False,
    diff_context: int = 3,
    max_diff_bytes: int = 20000,
    diff_evidence_enabled: bool = True,
    resume_state: PipelineState | None = None,
    rerun_completed: bool = False,
    save_path=None,
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

    from pathlib import Path as _Path
    if resume_state is not None:
        # Continue a saved run: reuse its stages/roles/task; completed stages keep
        # their artifacts and don't re-run (unless --rerun-completed).
        state = resume_state
        role_list = state.roles
        task = state.task
    else:
        state = plan_pipeline(
            task, role_list, write_capable=write_capable, free=free, offline=offline,
            provider=cfg.provider, model=cfg.resolved_model(), badge=badge, dry_run=dry_run,
        )
        state.root = str(_Path(root).resolve())
        # Snapshot the git state so a later --resume can detect a moved tree.
        if not dry_run:
            import datetime as _dt

            from . import __version__
            from .pipeline_git_snapshot import git_snapshot
            state.git_snapshot = git_snapshot(
                root, timestamp=_dt.datetime.now().isoformat(timespec="seconds"),
                version=__version__).model_dump()

    if dry_run:
        if console is not None:
            render_pipeline_plan(console, state)
        return state

    def _save() -> None:
        if save_path is not None:
            from .pipeline_state_io import save_state
            save_state(state, save_path)

    runner = stage_runner or _default_stage_runner
    prior_artifacts: list[dict] = []
    for i, role in enumerate(role_list):
        stage = state.stages[i]
        # On resume, keep an already-completed stage (and feed its artifact forward)
        # without re-running it, unless the user asked to rerun completed stages.
        if stage.status == COMPLETED and not rerun_completed:
            if stage.artifact:
                prior_artifacts.append(stage.artifact)
            continue
        stage.status = ACTIVE
        if console is not None:
            console.print(f"  [yellow]▶[/yellow] [bold]{role}[/bold] "
                          f"[dim]— {_PHASE.get(role, 'working')}[/dim]", highlight=False)
        ro = stage_read_only(role, write_capable=write_capable)
        prompt = _stage_prompt(task, role, prior_artifacts, write_capable=write_capable)
        try:
            outcome = runner(cfg, role, prompt, read_only=ro, root=root,
                             console=console, max_iterations=max_iterations)
        except Exception as exc:  # noqa: BLE001 — one stage's crash shouldn't nuke the report
            stage.status = FAILED
            stage.summary = f"stage error: {exc}"
            _save()
            break
        stage.summary = outcome.summary
        stage.files_changed = list(outcome.files_changed)
        stage.commands_requested = list(outcome.commands_requested)
        stage.test_result = outcome.test_result
        if outcome.artifact:
            stage.artifact = outcome.artifact  # preserved even if a later stage fails
        if outcome.blocked:
            stage.status = BLOCKED
            _save()
            break  # stop — do not silently continue past a blocked stage
        if not outcome.success:
            stage.status = FAILED
            _save()
            break
        stage.status = COMPLETED
        if outcome.artifact:
            prior_artifacts.append(outcome.artifact)
        _save()  # checkpoint after every successful stage

    if state.stopped:  # mark the un-run tail as skipped (artifacts so far preserved)
        for s in state.stages:
            if s.status == PENDING:
                s.status = SKIPPED

    ver = state.verification()  # verifier's report, else tester's
    if ver is not None:
        state.verdict = ver.final_verdict

    # Wave 8: capture the real working-tree diff (read-only) as evidence for the
    # semantic check + the truth table.
    from .pipeline_diff import capture_diff
    state.diff_evidence = capture_diff(
        root, context=diff_context, max_bytes=max_diff_bytes,
        include=diff_evidence_enabled).model_dump()

    # Wave 6: cross-artifact contract checks (always, from whatever artifacts exist).
    from .pipeline_contract import check_contract
    state.contract = check_contract(
        state.architect_plan(), state.implementation_report(),
        state.review_report(), ver,
    ).model_dump()

    # Independent verification (Wave 8: one or more gated suites). Sources, in
    # order: explicit --verify-suite / --verify-cmd; else --auto-verify-all
    # (several detected suites); else a single auto-detected test command.
    from .pipeline_verify import (
        aggregate_verify,
        auto_detect_suites,
        auto_detect_verify_command,
        parse_verify_suite,
        reconcile_with_tester,
        run_suites,
    )
    suite_specs: list[tuple[str, str]] = []
    if not independent_verify_enabled or state.stopped:
        state.verify_source = "disabled" if not independent_verify_enabled else "not_found"
    else:
        explicit = [parse_verify_suite(s) for s in (verify_suites or [])]
        cmds = ([verify_cmd] if verify_cmd else []) + list(verify_cmds or [])
        for i, c in enumerate(cmds):
            explicit.append(("tests" if i == 0 and not (verify_suites or []) else f"verify-{i + 1}", c))
        if explicit:
            suite_specs, state.verify_source = explicit, "provided"
        elif auto_verify_all:
            suite_specs = auto_detect_suites(root)
            state.verify_source = "detected" if suite_specs else "not_found"
        elif auto_verify_enabled:
            detected = auto_detect_verify_command(root)
            if detected is not None:
                suite_specs, state.verify_source = [("tests", detected[0])], "detected"
            else:
                state.verify_source = "not_found"
        else:
            state.verify_source = "not_found"

    if suite_specs:
        suite_runs = run_suites(suite_specs, root, timeout=verify_timeout,
                                yolo=getattr(cfg, "full_access", False), console=console)
        state.suites = [s.model_dump() for s in suite_runs]
        iv = aggregate_verify(suite_runs)
        state.independent_verify = iv.model_dump()
        note = reconcile_with_tester(iv, state.verdict)
        if note and console is not None:
            console.print(f"  [#e0af68]⚠ {note}[/#e0af68]")

    # Optional semantic contract: a read-only model pass judging whether the diff
    # actually fulfils the plan. Opt-in (--semantic-contract) to avoid extra calls.
    if semantic_enabled and not state.stopped:
        from .pipeline_semantic import check_semantic_contract

        def _judge(prompt: str) -> str:
            from ronin_agent_patterns import Message

            from .runner import build_provider
            return build_provider(cfg).complete(
                system="You are a careful, read-only semantic reviewer. Judge alignment "
                       "honestly and admit uncertainty.",
                messages=[Message(role="user", content=prompt)], tools=[], max_tokens=700,
            ).text

        sem = check_semantic_contract(state.architect_plan(), state.implementation_report(),
                                      state.review_report(), judge_runner=_judge,
                                      diff_evidence=state.diff_evidence)
        state.semantic = sem.model_dump()

    state.final_verdict = compute_final_verdict(state)
    state.final_recommendation = _final_recommendation(state)
    _save()  # final checkpoint
    return state
