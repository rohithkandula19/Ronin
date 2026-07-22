"""Server-side approval gate for the Coding World — the safety seam.

This module is the proof that a web action cannot bypass the terminal's safety
guarantees. It does NOT reimplement the floor: it imports the SAME functions
the CLI uses — `ronin_cli.approvals.is_floored_tool_call`, the runtime's
`SENSITIVE_TOOLS`, and `ronin_cli.roles.role_is_read_only` — and composes them
in the same order `code_mode`'s gate does:

    1. a floored (destructive/payment) tool call is DENIED regardless of what
       the human/front-end decided — the floor is not waivable;
    2. a read-only role can never obtain a write;
    3. a sensitive tool requires an explicit human approval decision;
    4. read-only tools pass without approval.

The interactive model-driven agent loop over a real provider is a separate,
credentialed concern (BLOCKED_CREDENTIALS in this environment); this gate is
the safety-critical decision layer, and it is fully testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Import the REAL runtime primitives — no duplication, no simulation.
from ronin_cli.approvals import is_floored_tool_call
from ronin_cli.code_tools import SENSITIVE_TOOLS
from ronin_cli.roles import role_is_read_only

_READONLY_TOOLS = {"read_file", "list_files", "search_files", "glob", "repo_map"}


class GateOutcome(str, Enum):
    allow = "allow"
    deny = "deny"
    needs_approval = "needs_approval"


@dataclass(frozen=True)
class GateDecision:
    outcome: GateOutcome
    reason: str
    floored: bool = False


def classify_tool_call(
    name: str,
    args: dict,
    *,
    role: str | None = None,
    human_decision: bool | None = None,
) -> GateDecision:
    """Decide a single tool call the same way the terminal would.

    `human_decision` is what the front-end approval card returned (True=approve,
    False=reject, None=not yet decided). It can turn a *needs_approval* into
    allow/deny — but it can NEVER override the floor or a read-only role.
    """
    is_sensitive = name in SENSITIVE_TOOLS

    # (1) The floor — checked for EVERY call, never waivable by the front-end.
    if is_floored_tool_call(name, args):
        return GateDecision(
            GateOutcome.deny,
            f"{name} hits the destructive/payment safety floor and requires the "
            "terminal's human floor gate; the web UI cannot approve it",
            floored=True,
        )

    # (2) Read-only roles can never write.
    if role_is_read_only(role) and is_sensitive:
        return GateDecision(
            GateOutcome.deny,
            f"role {role!r} is read-only and cannot perform the sensitive tool {name!r}",
        )

    # (4) Read-only tools pass without approval.
    if not is_sensitive and name not in _READONLY_TOOLS:
        # Unknown, non-sensitive tool: allow (reads/searches/etc.).
        return GateDecision(GateOutcome.allow, f"{name} is not a sensitive tool")
    if name in _READONLY_TOOLS:
        return GateDecision(GateOutcome.allow, f"{name} is read-only")

    # (3) Sensitive tool: requires an explicit human decision.
    if human_decision is None:
        return GateDecision(
            GateOutcome.needs_approval,
            f"{name} is a sensitive action and needs explicit approval",
        )
    if human_decision:
        return GateDecision(GateOutcome.allow, f"{name} approved by the user")
    return GateDecision(GateOutcome.deny, f"{name} rejected by the user")


def build_gate_cb(role: str | None, approvals: dict[str, bool]):
    """Build a `gate_cb(name, args) -> bool | str` for `run_code_agent`.

    `approvals` maps a per-call key to the human's decision. The returned
    callback enforces the floor and read-only rules server-side before ever
    consulting the human decision, so a compromised or buggy front-end that
    sends `approve` for everything still cannot execute a floored command.
    Returning a string denies with feedback (the runtime's contract).
    """

    def gate_cb(name: str, args: dict):
        decision = classify_tool_call(
            name, args, role=role, human_decision=approvals.get(name)
        )
        if decision.outcome is GateOutcome.allow:
            return True
        # deny or needs_approval both block execution now; the reason is fed back.
        return decision.reason

    return gate_cb
