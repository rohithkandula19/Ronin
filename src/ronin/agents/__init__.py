"""modes and extensibility: plan mode, subagents from markdown, and hooks.

Three ways to change what the agent may do, and all three work by *narrowing or
intercepting the tool layer* rather than by adding instructions to a prompt. That
is the single idea this package is built on. A model that is told not to do
something will do it eventually; a model handed a registry without ``write`` in it
cannot.

* :mod:`~ronin.agents.plan_mode` — a registry containing only read-only tools, a
  tolerant parser for the plan the model writes, and the approval that flips the
  session into execute mode with that plan pinned as the standing objective.
* :mod:`~ronin.agents.definitions` — subagents declared as ``.ronin/agents/*.md``
  with stdlib-parsed frontmatter, the four builtins (``explorer``, ``reviewer``,
  ``test-writer``, ``fixer``), a path-restriction wrapper that is why
  ``test-writer`` cannot edit ``src/``, and a dispatch fallback that answers
  ``None`` rather than guessing.
* :mod:`~ronin.agents.hooks` — shell commands on ``PreToolUse``, ``PostToolUse``,
  ``UserPromptSubmit``, ``Stop`` and ``SessionStart``, where exit code 2 blocks the
  action and hands its stderr to the model as the reason.

Imports ``ronin.core`` and ``ronin.tools`` only. A model *role* arrives as a name
(:data:`~ronin.agents.plan_mode.PLAN_MODEL_ROLE`, ``AgentDefinition.model``) and the
orchestrator resolves it — nothing here may see ``ronin.providers``.
"""

from __future__ import annotations

from .definitions import (
    AGENTS_SUBDIR,
    BUILTIN_AGENTS,
    FRONTMATTER_FENCE,
    KNOWN_KEYS,
    MODEL_ROLES,
    MUTATING_TOOL_NAMES,
    PATH_ARG_KEYS,
    REQUIRED_KEYS,
    SELECTION_FLOOR,
    AgentDefinition,
    FrontmatterError,
    RestrictedPaths,
    build_subagent_registry,
    gated_tools,
    load_agents,
    load_definition,
    load_directory,
    model_roles,
    parse_definition,
    parse_frontmatter,
    path_allowed,
    rank,
    restrict_writes,
    score_match,
    select,
    subagent_catalogue,
)
from .hooks import (
    BLOCK_EXIT_CODE,
    BLOCKING_EVENTS,
    DEFAULT_TIMEOUT_SECONDS,
    MATCH_ALL,
    MAX_HOOK_OUTPUT_CHARS,
    TOOL_EVENTS,
    HookCompletion,
    HookConfig,
    HookConfigError,
    HookEvent,
    HookInvocation,
    HookOutcome,
    HookProcess,
    HookReport,
    HookRun,
    HookRunner,
    HookSpec,
    event_payload,
    load_hook_config,
    run_shell_hook,
)
from .plan_mode import (
    FORBIDDEN_IN_PLAN_MODE,
    MAX_PLAN_STEPS,
    PLAN_MODE_PROMPT,
    PLAN_MODE_TOOLS,
    PLAN_MODEL_ROLE,
    Plan,
    PlanApproval,
    PlanModeSetup,
    PlanModeViolation,
    PlanParseError,
    PlanStep,
    approve,
    assert_read_only,
    enter_plan_mode,
    objective_message,
    parse_plan,
    pinned_objective,
    plan_registry,
    reject,
)

__all__ = [
    "AGENTS_SUBDIR",
    "BLOCKING_EVENTS",
    "BLOCK_EXIT_CODE",
    "BUILTIN_AGENTS",
    "DEFAULT_TIMEOUT_SECONDS",
    "FORBIDDEN_IN_PLAN_MODE",
    "FRONTMATTER_FENCE",
    "KNOWN_KEYS",
    "MATCH_ALL",
    "MAX_HOOK_OUTPUT_CHARS",
    "MAX_PLAN_STEPS",
    "MODEL_ROLES",
    "MUTATING_TOOL_NAMES",
    "PATH_ARG_KEYS",
    "PLAN_MODEL_ROLE",
    "PLAN_MODE_PROMPT",
    "PLAN_MODE_TOOLS",
    "REQUIRED_KEYS",
    "SELECTION_FLOOR",
    "TOOL_EVENTS",
    "AgentDefinition",
    "FrontmatterError",
    "HookCompletion",
    "HookConfig",
    "HookConfigError",
    "HookEvent",
    "HookInvocation",
    "HookOutcome",
    "HookProcess",
    "HookReport",
    "HookRun",
    "HookRunner",
    "HookSpec",
    "Plan",
    "PlanApproval",
    "PlanModeSetup",
    "PlanModeViolation",
    "PlanParseError",
    "PlanStep",
    "RestrictedPaths",
    "approve",
    "assert_read_only",
    "build_subagent_registry",
    "enter_plan_mode",
    "event_payload",
    "gated_tools",
    "load_agents",
    "load_definition",
    "load_directory",
    "load_hook_config",
    "model_roles",
    "objective_message",
    "parse_definition",
    "parse_frontmatter",
    "parse_plan",
    "path_allowed",
    "pinned_objective",
    "plan_registry",
    "rank",
    "reject",
    "restrict_writes",
    "run_shell_hook",
    "score_match",
    "select",
    "subagent_catalogue",
]
