"""Agent swarm — a task worked by a *team* of role-specialized agents, each on a
possibly different vendor's model. Cooperative intelligence across providers.

A single agent is a generalist. A swarm splits the work the way a good team does:
an **architect** plans (read-only), an **implementer** writes the code, a
**reviewer** critiques the diff (read-only), and the implementer does one revision
pass to address the review. Because ronin is provider-agnostic, each role can run
on a different model — Claude architects, a fast free model implements, Gemini
reviews — a "dream team" no single-vendor agent can assemble.

Roster parsing + role resolution are pure and unit-tested; the pipeline runs the
real agents.
"""
from __future__ import annotations

from dataclasses import dataclass

# Role system-prompt shaping. Each role gets the coding agent's full toolbelt;
# these just steer its mindset.
_ARCHITECT_SYS = (
    "You are the ARCHITECT on a team. Explore read-only and produce a precise, "
    "minimal implementation PLAN: the exact files to change and what to change in "
    "each, plus any test to add. Do NOT write code — hand off a plan the "
    "implementer can follow exactly."
)
_REVIEWER_SYS = (
    "You are the REVIEWER on a team. Read the diff and the surrounding code and "
    "critique it: correctness bugs, missed edge cases, broken tests, anything that "
    "doesn't match the task. Be specific and concise. If it's solid, say so."
)

ROLES = ("architect", "implementer", "reviewer")


@dataclass
class RoleAssignment:
    architect: str | None = None     # provider[:model] or None → base config
    implementer: str | None = None
    reviewer: str | None = None

    def for_role(self, role: str) -> str | None:
        return getattr(self, role, None)


def parse_roster(spec: str | None) -> RoleAssignment:
    """Parse 'architect=anthropic,implementer=cerebras:gpt-oss-120b,reviewer=gemini'
    into a RoleAssignment. Unknown roles are ignored; missing ones stay None
    (→ run on the base config). Pure."""
    ra = RoleAssignment()
    if not spec:
        return ra
    for part in spec.split(","):
        if "=" not in part:
            continue
        role, _, prov = part.partition("=")
        role, prov = role.strip().lower(), prov.strip()
        if role in ROLES and prov:
            setattr(ra, role, prov)
    return ra


def _cfg_for(base, spec: str | None):
    """Derive the config for a role: a provider override, or the base config."""
    if not spec:
        return base
    from .consensus import parse_model_spec
    from .runner import config_for_spec
    return config_for_spec(base, parse_model_spec(spec))


def role_label(base, ra: RoleAssignment, role: str) -> str:
    cfg = _cfg_for(base, ra.for_role(role))
    return f"{cfg.provider}:{cfg.resolved_model()}"


def run_swarm(config, task: str, root, console, *, roster: RoleAssignment | None = None,
              max_iterations: int = 20, yolo: bool = False):
    """Run the architect → implementer → reviewer → revise pipeline. Returns the
    implementer's final AgentResult."""
    from .code_mode import run_code_agent

    ra = roster or RoleAssignment()
    arch_cfg = _cfg_for(config, ra.architect)
    impl_cfg = _cfg_for(config, ra.implementer)
    rev_cfg = _cfg_for(config, ra.reviewer)

    # 1. architect plans (read-only)
    console.print(f"[#7dcfff]◆ architect[/#7dcfff] [dim]{arch_cfg.provider}:{arch_cfg.resolved_model()}[/dim]")
    plan = run_code_agent(arch_cfg, f"TASK: {task}\n\nProduce the implementation plan.",
                          root=root, console=console, read_only=True,
                          base_system=_ARCHITECT_SYS, max_iterations=max_iterations,
                          include_image_tool=False)

    # 2. implementer executes the plan
    console.print(f"[#bb9af7]◆ implementer[/#bb9af7] [dim]{impl_cfg.provider}:{impl_cfg.resolved_model()}[/dim]")
    impl = run_code_agent(
        impl_cfg, f"TASK: {task}\n\nFollow this plan from the architect:\n{plan.output}\n\n"
        "Implement it now.", root=root, console=console, yolo=yolo,
        max_iterations=max_iterations)

    # 3. reviewer critiques (read-only)
    console.print(f"[#9ece6a]◆ reviewer[/#9ece6a] [dim]{rev_cfg.provider}:{rev_cfg.resolved_model()}[/dim]")
    review = run_code_agent(
        rev_cfg, f"TASK: {task}\n\nThe implementer just made changes. Review them "
        "(use git_diff and read the files). List concrete problems, or confirm it's good.",
        root=root, console=console, read_only=True, base_system=_REVIEWER_SYS,
        max_iterations=max_iterations, include_image_tool=False)

    # 4. implementer addresses the review (one revision pass)
    console.print(f"[#bb9af7]◆ implementer (revise)[/#bb9af7] [dim]addressing review[/dim]")
    final = run_code_agent(
        impl_cfg, f"TASK: {task}\n\nThe reviewer said:\n{review.output}\n\n"
        "Address any real issues they raised. If there are none, leave it as-is and say so.",
        root=root, console=console, yolo=yolo, max_iterations=max_iterations)
    return final
