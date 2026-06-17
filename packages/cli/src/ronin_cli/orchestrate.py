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


def build_subagents(
    base: RoninConfig,
    roster: dict[str, str | None],
    *,
    tools_for_role: "dict[str, list[Tool]] | None" = None,
    roles: "dict[str, str] | None" = None,
    descriptions: "dict[str, str] | None" = None,
    max_iterations: int = 12,
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
) -> OrchestrateOutcome:
    """Run the orchestrator on ``goal``.

    Each role's provider is resolved from ``roster_spec`` (role=provider[:model],
    comma-separated). The planner/synthesizer run on the base config's provider.

    When ``read_only`` is False the mutating sub-agents (implementer) work inside
    an isolated git worktree — the SAME ``git_worktree`` isolation the dojo uses —
    so their edits never touch the main checkout; the captured diff is appended to
    the result. Read-only runs explore in place (no worktree needed).
    """
    if not read_only:
        from .worktree import is_git_repo
        if is_git_repo(root):
            return _run_in_worktree(
                base, goal, roster_spec=roster_spec, root=root, on_step=on_step,
                on_subtask_start=on_subtask_start, max_iterations=max_iterations,
            )
        # Not a git repo: fall back to read-only so we never mutate the tree
        # uncontrolled. The CLI surfaces this; here we just stay safe.
        read_only = True

    tools_for_role = _tools_for_roles(base, root, read_only=read_only, mutate_root=root)
    return _run_core(
        base, goal, roster_spec=roster_spec, tools_for_role=tools_for_role,
        on_step=on_step, on_subtask_start=on_subtask_start,
        max_iterations=max_iterations, diff="",
    )


def _run_in_worktree(base, goal, *, roster_spec, root, on_step, on_subtask_start,
                     max_iterations):
    """Run the mutating orchestration inside a throwaway worktree, capture the
    diff, and clean up — reusing the dojo's worktree isolation."""
    from .worktree import git_worktree, worktree_diff

    with git_worktree(root, label="orchestrate") as wt:
        tools_for_role = _tools_for_roles(base, wt, read_only=False, mutate_root=wt)
        outcome = _run_core(
            base, goal, roster_spec=roster_spec, tools_for_role=tools_for_role,
            on_step=on_step, on_subtask_start=on_subtask_start,
            max_iterations=max_iterations, diff="",
        )
        outcome.diff = worktree_diff(wt)
    return outcome


def _run_core(base, goal, *, roster_spec, tools_for_role, on_step, on_subtask_start,
              max_iterations, diff):
    roster = parse_roster(roster_spec)
    subs = build_subagents(
        base, roster, tools_for_role=tools_for_role, max_iterations=max_iterations,
    )
    orch = OrchestratorAgent(
        provider=provider_for_spec(base, None),  # planner/synth = base provider
        sub_agents=subs,
    )
    result = orch.run(goal, on_step=on_step, on_subtask_start=on_subtask_start)
    return OrchestrateOutcome(
        success=result.success,
        output=result.output,
        plan_subtasks=[st.model_dump() for st in (result.plan.subtasks if result.plan else [])],
        subtask_results=[r.model_dump() for r in result.subtask_results],
        error=result.error,
        diff=diff,
    )


def _tools_for_roles(
    base: RoninConfig, root: Path | str, *, read_only: bool,
    mutate_root: "Path | str | None" = None,
) -> dict[str, list[Tool]]:
    """Build the per-role tool subsets from ronin's code toolbelt.

    Researcher/reviewer always get read-only explorer tools bound to ``root``.
    The implementer gets the FULL code toolbelt bound to ``mutate_root`` (the
    isolated worktree) only when the run is not read-only. Imported lazily so the
    pure roster/provider helpers don't pull in the heavy code-tools graph."""
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
    out: dict[str, list[Tool]] = {
        "researcher": readonly_tools,
        "reviewer": readonly_tools,
    }
    if read_only:
        out["implementer"] = readonly_tools
        out["tester"] = tester_readonly
    else:
        # Full toolbelt bound to the isolated worktree, so mutations (and any new
        # test files the tester writes) land there, not the main checkout.
        worktree_tools = build_code_tools(mutate_root or root, sandbox=sandbox)
        out["implementer"] = worktree_tools
        out["tester"] = worktree_tools
    return out
