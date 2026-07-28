"""CLI bridge for the multi-agent orchestrator.

The orchestrator core lives in ``ronin_agent_patterns.OrchestratorAgent`` — it
decomposes a goal into assigned subtasks and runs provider-agnostic sub-agents
(parallel where independent), then synthesizes. This module wires that core into
ronin's existing machinery:

- per-role PROVIDER assignment via ``config_for_spec`` + ``build_single_provider``
  (the same provider selection the dojo, swarm, and consensus use), so each
  sub-agent really can sit on a different vendor's model;
- WORKTREE-ISOLATED code-editing sub-agents reusing ``git_worktree`` (the same
  isolation the dojo uses), so parallel mutating sub-agents don't collide.

Roster parsing and provider/spec resolution are pure and unit-tested; the run
drives the real core orchestrator. With ``--offline`` (or no keys + a local
brain) the whole thing runs with zero egress.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ronin_agent_patterns import (
    LLMProvider,
    OrchestratorAgent,
    OrchestratorSubAgent,
    Tool,
)

from .agent_catalog import (
    AgentProfile,
    ProfileSelection,
    build_catalog,
    repository_signals,
    select_profiles_for_repository,
)
from .agent_governance import OrchestrationGovernance, provider_health
from .agent_state import AgentTaskStateStore
from .agent_workflow import WorkflowContract, inspect_handoffs
from .config import RoninConfig

# Default specialist roster. Each is provider-agnostic: the planner assigns
# subtasks to these roles, and each role can be pinned to a different model via
# the --roster flag. Kept small and generic on purpose.
_RESEARCHER_SYS = (
    "You are the RESEARCHER. Investigate the codebase or question read-only and "
    "report concrete findings: file paths, function names, how things connect. Do "
    "not propose edits — just surface the facts the rest of the team needs."
)
_IMPLEMENTER_SYS = (
    "You are the IMPLEMENTER. Make the requested change in your isolated worktree: "
    "read, edit, create files, run commands. Keep it minimal and correct. When "
    "done, summarise what you changed and why."
)
_REVIEWER_SYS = (
    "You are the REVIEWER. Read the proposed change and the surrounding code and "
    "critique it: correctness bugs, missed cases, broken tests. Be specific. If "
    "it is solid, say so."
)
_TESTER_SYS = (
    "You are the TESTER. Write or extend tests for the change in your isolated "
    "worktree and run them. Report exactly which commands you ran, whether they "
    "passed, and any failures with the relevant output. Do not change production "
    "code to make a test pass — surface real failures."
)

DEFAULT_ROLES: dict[str, str] = {
    "researcher": _RESEARCHER_SYS,
    "implementer": _IMPLEMENTER_SYS,
    "reviewer": _REVIEWER_SYS,
    "tester": _TESTER_SYS,
}

DEFAULT_DESCRIPTIONS: dict[str, str] = {
    "researcher": "explores read-only and reports facts about the code/question",
    "implementer": "writes and edits code to make a change",
    "reviewer": "reviews a change for correctness and completeness",
    "tester": "writes and runs tests for a change and reports pass/fail",
}

_CORE_TIERS = {
    "researcher": "explore",
    "implementer": "write",
    "reviewer": "explore",
    "tester": "test",
}


def core_agent_profiles() -> tuple[AgentProfile, ...]:
    """Return Ronin's always-available orchestration roles as catalog profiles."""
    return tuple(AgentProfile(
        key=role,
        description=DEFAULT_DESCRIPTIONS[role],
        system=system,
        tags=(role,),
        tier=_CORE_TIERS[role],
        source="core",
    ) for role, system in DEFAULT_ROLES.items())


def agent_catalog(root: Path | str, *, manifest: Path | str | None = None) -> tuple[AgentProfile, ...]:
    """Return the scalable specialist catalog for a repository."""
    return build_catalog(core_agent_profiles(), root, manifest=manifest)


def select_agents_for_goal(
    goal: str,
    root: Path | str,
    *,
    max_agents: int = 8,
    manifest: Path | str | None = None,
) -> tuple[AgentProfile, ...]:
    """Select the bounded specialist team to expose to an orchestration planner."""
    return select_agents_with_context(
        goal, root, max_agents=max_agents, manifest=manifest,
    ).profiles


def select_agents_with_context(
    goal: str,
    root: Path | str,
    *,
    max_agents: int = 8,
    manifest: Path | str | None = None,
) -> ProfileSelection:
    """Return the selected team plus explainable local repository evidence."""
    profiles = agent_catalog(root, manifest=manifest)
    return select_profiles_for_repository(
        goal, profiles,
        core_keys=DEFAULT_ROLES,
        repository=repository_signals(goal, root),
        max_agents=max_agents,
    )


@dataclass
class RoleProvider:
    """A role bound to an optional provider spec ('anthropic', 'gemini:...')."""

    role: str
    spec: str | None = None  # None → run on the base config's provider


def parse_roster(spec: str | None) -> dict[str, str | None]:
    """Parse 'researcher=anthropic,implementer=cerebras:gpt-oss-120b,reviewer=gemini'
    into {role: provider_spec_or_None}. Roles not listed get None (→ base config).
    Unknown roles are kept (so a custom plan can reference them). Pure."""
    out: dict[str, str | None] = {}
    if not spec:
        return out
    for part in spec.split(","):
        if "=" not in part:
            continue
        role, _, prov = part.partition("=")
        role, prov = role.strip().lower(), prov.strip()
        if role:
            out[role] = prov or None
    return out


def provider_for_spec(base: RoninConfig, spec: str | None) -> LLMProvider:
    """Build a real ``LLMProvider`` for a role's provider spec.

    ``None`` → the base config's provider. Otherwise resolve via the same
    ``parse_model_spec`` + ``config_for_spec`` + ``build_single_provider`` chain
    the dojo/consensus use, so a role can be pinned to any vendor/model ronin
    knows. Offline mode is honoured (the config is forced to a local brain)."""
    from .consensus import parse_model_spec
    from .runner import build_single_provider, config_for_spec

    if not spec:
        cfg = base
    else:
        cfg = config_for_spec(base, parse_model_spec(spec))
    if cfg.offline:
        from .offline import apply_offline
        cfg = apply_offline(cfg)
    return build_single_provider(cfg)


def role_label(base: RoninConfig, spec: str | None) -> str:
    from .consensus import parse_model_spec
    from .runner import config_for_spec

    cfg = base if not spec else config_for_spec(base, parse_model_spec(spec))
    return f"{cfg.provider}:{cfg.resolved_model()}"


def roster_labels(
    base: RoninConfig,
    roster_spec: str | None,
    profiles: "list[AgentProfile] | tuple[AgentProfile, ...] | None" = None,
) -> dict[str, str]:
    """Map each built-in specialist role to the provider:model it will run on.

    This is the sub-agent tree the dashboard renders: the planner/synthesizer run
    on the base provider, and each role on its own (possibly different) vendor's
    model via ``--roster``. Pure label resolution — no providers are built."""
    parsed = parse_roster(roster_spec)
    roles = [profile.key for profile in profiles] if profiles is not None else list(DEFAULT_ROLES)
    return {role: role_label(base, parsed.get(role)) for role in roles}


def score_final_faithfulness(outcome: "OrchestrateOutcome") -> "dict[str, Any] | None":
    """Score how grounded the synthesized final answer is in its sub-agents' work.

    The synthesizer writes the final answer FROM the completed subtask outputs
    (see ``OrchestratorAgent._synthesize`` — its prompt is built from exactly
    those outputs). So those outputs ARE the sources the final answer must be
    grounded in. We run the offline faithfulness harness over the answer against
    them and return ``{score, grounded, abstain}`` — real lexical/symbol
    grounding, not a canned number. Returns ``None`` when there is nothing to
    score (no answer or no source outputs)."""
    answer = (outcome.output or "").strip()
    source_texts = [
        r.get("output", "")
        for r in outcome.subtask_results
        if (r.get("output") or "").strip()
    ]
    if not answer or not source_texts:
        return None
    try:
        from ronin_hardening import FaithfulnessHarness, Source

        sources = [Source(origin=f"subtask:{i}", text=t) for i, t in enumerate(source_texts)]
        report = FaithfulnessHarness().check(answer, sources)
    except Exception:  # noqa: BLE001 — scoring must never break a run
        return None
    return {
        "score": round(report.score, 3),
        "grounded": (not report.abstain) and report.score >= 0.5,
        "abstain": bool(report.abstain),
        "reason": report.reason,
        "n_sources": report.n_sources,
    }


def build_subagents(
    base: RoninConfig,
    roster: dict[str, str | None],
    *,
    tools_for_role: "dict[str, list[Tool]] | None" = None,
    roles: "dict[str, str] | None" = None,
    descriptions: "dict[str, str] | None" = None,
    max_iterations: int = 12,
    max_total_tokens: int | None = None,
    max_wall_time_seconds: float | None = None,
) -> list[OrchestratorSubAgent]:
    """Build the provider-agnostic sub-agent roster for the orchestrator.

    ``roster`` maps role → provider spec (None = base provider). ``roles`` maps
    role → system prompt (defaults to ``DEFAULT_ROLES``). ``tools_for_role`` lets
    the caller hand each role a tool subset (e.g. read-only explorer tools for
    the researcher, full code tools for the implementer)."""
    roles = roles or DEFAULT_ROLES
    descriptions = descriptions or DEFAULT_DESCRIPTIONS
    tools_for_role = tools_for_role or {}
    subs: list[OrchestratorSubAgent] = []
    for role, system in roles.items():
        spec = roster.get(role)
        subs.append(OrchestratorSubAgent(
            role=role,
            description=descriptions.get(role, role),
            system=system,
            tools=tools_for_role.get(role, []),
            provider=provider_for_spec(base, spec),
            max_iterations=max_iterations,
            max_total_tokens=max_total_tokens,
            max_wall_time_seconds=max_wall_time_seconds,
        ))
    return subs


@dataclass
class OrchestrateOutcome:
    success: bool
    output: str
    plan_subtasks: list[dict[str, Any]] = field(default_factory=list)
    subtask_results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    # In write mode, the diff captured from the isolated worktree (empty otherwise).
    diff: str = ""
    agent_profiles: tuple[AgentProfile, ...] = ()
    workflow: dict[str, Any] = field(default_factory=dict)
    governance: dict[str, Any] = field(default_factory=dict)
    provider_health: dict[str, dict[str, Any]] = field(default_factory=dict)
    handoffs: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None


def run_orchestrate(
    base: RoninConfig,
    goal: str,
    *,
    roster_spec: str | None = None,
    root: Path | str = ".",
    on_step=None,
    on_subtask_start=None,
    max_iterations: int = 12,
    read_only: bool = True,
    profiles: "tuple[AgentProfile, ...] | list[AgentProfile] | None" = None,
    max_agents: int = 8,
    agent_manifest: Path | str | None = None,
    workflow: WorkflowContract | None = None,
    governance: OrchestrationGovernance | None = None,
    persist_state: bool = True,
) -> OrchestrateOutcome:
    """Run the orchestrator on ``goal``.

    Each role's provider is resolved from ``roster_spec`` (role=provider[:model],
    comma-separated). The planner/synthesizer run on the base config's provider.

    When ``read_only`` is False the mutating sub-agents (implementer) work inside
    an isolated git worktree — the SAME ``git_worktree`` isolation the dojo uses —
    so their edits never touch the main checkout; the captured diff is appended to
    the result. Read-only runs explore in place (no worktree needed).
    """
    governance = governance or OrchestrationGovernance(
        max_agents=max_agents,
        max_parallel=min(4, max_agents),
        max_total_subtask_iterations=max(12, max_agents * max_iterations),
        max_iterations_per_agent=max_iterations,
    )
    try:
        selection = (
            ProfileSelection(tuple(profiles), {p.key: ("explicit profile",) for p in profiles})
            if profiles is not None else select_agents_with_context(
                goal, root, max_agents=max_agents, manifest=agent_manifest,
            )
        )
        selected = selection.profiles
        governance.validate_team(len(selected))
    except ValueError as exc:
        return OrchestrateOutcome(success=False, output="", error=str(exc))
    if not selected:
        return OrchestrateOutcome(success=False, output="", error="orchestration needs at least one agent profile")

    state_store: AgentTaskStateStore | None = AgentTaskStateStore(root) if persist_state else None
    state: dict[str, Any] | None = None
    if state_store is not None:
        state = state_store.create(
            goal,
            selection={
                "profiles": [profile.key for profile in selected],
                "reasons": {key: list(value) for key, value in selection.reasons.items()},
                "repository": {
                    "tags": list(selection.repository.tags),
                    "relevant_files": list(selection.repository.relevant_files),
                    "symbols": list(selection.repository.symbols),
                },
            },
            workflow=workflow.as_dict() if workflow else None,
            governance=governance.as_dict(),
        )

    def _on_step(step) -> None:
        if state_store is not None and state is not None:
            state_store.apply_step(state, step)
        if on_step is not None:
            on_step(step)

    def _on_subtask_start(subtask, label) -> None:
        if state_store is not None and state is not None:
            state_store.mark_started(state, subtask, label)
        if on_subtask_start is not None:
            on_subtask_start(subtask, label)

    if not read_only:
        from .worktree import is_git_repo
        if is_git_repo(root):
            try:
                outcome = _run_in_worktree(
                    base, goal, roster_spec=roster_spec, root=root, on_step=_on_step,
                    on_subtask_start=_on_subtask_start, max_iterations=max_iterations,
                    profiles=selected, workflow=workflow, governance=governance,
                )
            except Exception as exc:  # noqa: BLE001 - state must survive provider/setup failures
                outcome = OrchestrateOutcome(
                    success=False, output="", error=f"{type(exc).__name__}: {exc}",
                    agent_profiles=selected,
                    workflow=workflow.as_dict() if workflow else {},
                    governance=governance.as_dict(),
                )
            return _finish_outcome(outcome, state_store, state)
        # Not a git repo: fall back to read-only so we never mutate the tree
        # uncontrolled. The CLI surfaces this; here we just stay safe.
        read_only = True

    tools_for_role = _tools_for_roles(
        base, root, read_only=read_only, mutate_root=root, profiles=selected,
    )
    try:
        outcome = _run_core(
            base, goal, roster_spec=roster_spec, tools_for_role=tools_for_role,
            on_step=_on_step, on_subtask_start=_on_subtask_start,
            max_iterations=max_iterations, diff="", profiles=selected,
            workflow=workflow, governance=governance,
        )
    except Exception as exc:  # noqa: BLE001 - return an honest, persisted setup failure
        outcome = OrchestrateOutcome(
            success=False, output="", error=f"{type(exc).__name__}: {exc}",
            agent_profiles=selected,
            workflow=workflow.as_dict() if workflow else {},
            governance=governance.as_dict(),
        )
    return _finish_outcome(outcome, state_store, state)


def _run_in_worktree(base, goal, *, roster_spec, root, on_step, on_subtask_start,
                     max_iterations, profiles, workflow, governance):
    """Run the mutating orchestration inside a throwaway worktree, capture the
    diff, and clean up — reusing the dojo's worktree isolation."""
    from .worktree import git_worktree, worktree_diff

    with git_worktree(root, label="orchestrate") as wt:
        tools_for_role = _tools_for_roles(
            base, wt, read_only=False, mutate_root=wt, profiles=profiles,
        )
        outcome = _run_core(
            base, goal, roster_spec=roster_spec, tools_for_role=tools_for_role,
            on_step=on_step, on_subtask_start=on_subtask_start,
            max_iterations=max_iterations, diff="", profiles=profiles,
            workflow=workflow, governance=governance,
        )
        outcome.diff = worktree_diff(wt)
    return outcome


def _run_core(base, goal, *, roster_spec, tools_for_role, on_step, on_subtask_start,
              max_iterations, diff, profiles, workflow, governance):
    roster = parse_roster(roster_spec)
    roles = {profile.key: profile.system for profile in profiles}
    descriptions = {profile.key: profile.description for profile in profiles}
    subs = build_subagents(
        base, roster, tools_for_role=tools_for_role, roles=roles,
        descriptions=descriptions,
        max_iterations=governance.max_iterations_per_agent,
        max_total_tokens=governance.max_tokens_per_agent,
        max_wall_time_seconds=governance.max_wall_time_seconds_per_agent,
    )
    orch = OrchestratorAgent(
        provider=provider_for_spec(base, None),  # planner/synth = base provider
        sub_agents=subs,
        goal_system=workflow.planner_instructions() if workflow else "",
        max_parallel=governance.max_parallel,
        required_roles=set(workflow.required_roles) if workflow else set(),
        role_dependencies={
            role: set(upstream) for role, upstream in (workflow.role_dependencies.items() if workflow else [])
        },
        acceptance_roles=(set(workflow.acceptance_roles)
                          if workflow and governance.require_independent_acceptance else set()),
        max_total_subtask_iterations=governance.max_total_subtask_iterations,
    )
    result = orch.run(goal, on_step=on_step, on_subtask_start=on_subtask_start)
    result_dicts = [r.model_dump() for r in result.subtask_results]
    return OrchestrateOutcome(
        success=result.success,
        output=result.output,
        plan_subtasks=[st.model_dump() for st in (result.plan.subtasks if result.plan else [])],
        subtask_results=result_dicts,
        error=result.error,
        diff=diff,
        agent_profiles=tuple(profiles),
        workflow=workflow.as_dict() if workflow else {},
        governance=governance.as_dict(),
        provider_health=provider_health(result_dicts),
        handoffs=inspect_handoffs(goal, result_dicts),
    )


def _finish_outcome(
    outcome: OrchestrateOutcome,
    state_store: AgentTaskStateStore | None,
    state: dict[str, Any] | None,
) -> OrchestrateOutcome:
    """Persist final state after either the normal or worktree execution path."""
    if state_store is not None and state is not None:
        state_store.finish(
            state,
            success=outcome.success,
            output=outcome.output,
            error=outcome.error,
            provider_health=outcome.provider_health,
            handoffs=outcome.handoffs,
        )
        outcome.run_id = str(state.get("id"))
    return outcome


def _tools_for_roles(
    base: RoninConfig, root: Path | str, *, read_only: bool,
    mutate_root: "Path | str | None" = None,
    profiles: "tuple[AgentProfile, ...] | list[AgentProfile] | None" = None,
) -> dict[str, list[Tool]]:
    """Build the per-role tool subsets from ronin's code toolbelt.

    Explorer profiles always get read-only tools. Test-tier profiles may execute
    existing commands in read-only mode and receive the full isolated-worktree
    toolbelt only in write mode. Write-tier profiles receive mutations only in
    that isolated worktree. Imported lazily so the pure catalog helpers do not
    pull in the heavy code-tools graph."""
    try:
        from .code_tools import build_code_tools
    except Exception:  # noqa: BLE001 — keep the orchestrator usable without code tools
        return {}

    _READONLY = {"read_file", "list_files", "search_files", "glob"}
    sandbox = not getattr(base, "full_access", False)
    base_tools = build_code_tools(root, sandbox=sandbox)
    readonly_tools = [t for t in base_tools if t.name in _READONLY]
    # The tester needs to actually RUN a suite, so it gets the explorer tools plus
    # run_command even in read-only mode (it can run an existing suite without
    # editing production code).
    tester_readonly = [
        t for t in base_tools if t.name in (_READONLY | {"run_command"})
    ]
    selected = tuple(profiles) if profiles is not None else core_agent_profiles()
    worktree_tools: list[Tool] | None = None
    out: dict[str, list[Tool]] = {}
    for profile in selected:
        if profile.tier == "explore":
            out[profile.key] = readonly_tools
            continue
        if read_only:
            out[profile.key] = tester_readonly if profile.tier == "test" else readonly_tools
            continue
        # Mutations and test writes are bound to the isolated worktree, never the
        # checkout from which the orchestrator was invoked.
        if worktree_tools is None:
            worktree_tools = build_code_tools(mutate_root or root, sandbox=sandbox)
        out[profile.key] = worktree_tools
    return out
