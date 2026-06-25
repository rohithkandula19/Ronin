"""First-class role agents for coding tasks.

A *role* shapes how ronin approaches a task — it adds guidance to the system
prompt and, for the read-only roles, restricts the agent to read-only tools. It
is **guidance, never a safety bypass**: every write/command still flows through
the same approval gate. The role is process-global session state (mirroring the
Shift+Tab edit mode), set with ``/role`` and shown in the chip strip + status.

Built-ins: researcher · implementer · reviewer · tester · architect · debugger.
Researcher, reviewer, and architect are read-only by default (they explore,
review, and design without editing); implementer, tester, and debugger may
propose edits / run commands — still gated.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    key: str
    label: str
    blurb: str          # one-liner for /role and /help
    read_only: bool     # True → the agent only gets read-only tools
    guidance: str       # appended to the system prompt when this role is active


ROLES: dict[str, Role] = {
    "researcher": Role(
        "researcher", "Researcher", "Explore code read-only",
        read_only=True,
        guidance=(
            "ROLE: Researcher. Explore the codebase READ-ONLY to gather context and "
            "answer the question. Map the relevant files, read them, and report "
            "concrete findings with file·line citations. Do NOT propose or make edits; "
            "produce understanding, not changes."
        ),
    ),
    "implementer": Role(
        "implementer", "Implementer", "Propose code changes",
        read_only=False,
        guidance=(
            "ROLE: Implementer. Make the requested change as focused, idiomatic edits. "
            "Read the surrounding code first, match its style, keep the diff minimal, "
            "and verify with tests/lint when possible. Every write and command is still "
            "shown for the user's approval — never assume it's auto-approved."
        ),
    ),
    "reviewer": Role(
        "reviewer", "Reviewer", "Review current diff",
        read_only=True,
        guidance=(
            "ROLE: Reviewer. Review the current changes READ-ONLY. Inspect the diff and "
            "the touched code for correctness bugs, risks, security issues, and missing "
            "tests. Report findings as file·line · severity · the problem · a suggested "
            "fix. Do NOT edit files — your job is the review, not the patch."
        ),
    ),
    "tester": Role(
        "tester", "Tester", "Verify with tests",
        read_only=False,
        guidance=(
            "ROLE: Tester. Verify behavior with tests. Detect and run the project's test "
            "command, surface failures with the real output, and recommend or add focused "
            "tests for gaps. Running commands is still gated — never claim green without a "
            "real passing run."
        ),
    ),
    "architect": Role(
        "architect", "Architect", "Design before coding",
        read_only=True,
        guidance=(
            "ROLE: Architect. Design the structure BEFORE any code is written. Explore "
            "the codebase read-only, then propose an approach: the modules/interfaces "
            "involved, trade-offs, and a step-by-step plan. Do NOT edit files; hand off a "
            "clear design the implementer role can execute."
        ),
    ),
    "debugger": Role(
        "debugger", "Debugger", "Root-cause failures",
        read_only=False,
        guidance=(
            "ROLE: Debugger. Investigate the failure and find its ROOT CAUSE before "
            "proposing a fix. Reproduce it (running commands is gated), read the stack "
            "trace and the implicated code, form and test hypotheses, and explain the "
            "actual cause — not just the symptom — before changing anything."
        ),
    ),
}


def parse_role(value: str | None) -> str | None:
    """Normalize a role name. Returns the canonical key, ``"clear"``, or None for
    an unknown value (so callers can show the valid set)."""
    if value is None:
        return None
    v = value.strip().lower()
    if v in ("clear", "none", "off", "reset"):
        return "clear"
    return v if v in ROLES else None


def role_is_read_only(key: str | None) -> bool:
    r = ROLES.get(key or "")
    return bool(r and r.read_only)


def role_guidance(key: str | None) -> str:
    r = ROLES.get(key or "")
    return r.guidance if r else ""


def role_help_lines() -> list[str]:
    """``/role <key>  <blurb>`` lines for the /help Roles section."""
    return [f"/role {r.key:<11} {r.blurb}" for r in ROLES.values()]


# --- session-global current role (mirrors prompt_box.current_mode) -----------

_current_role: str | None = None


def current_role() -> str | None:
    return _current_role


def set_role(key: str | None) -> str | None:
    """Set (or with ``None``/``"clear"`` clear) the active role. Returns the new
    current role. Unknown keys are ignored (current role unchanged)."""
    global _current_role
    if key is None or key == "clear":
        _current_role = None
    elif key in ROLES:
        _current_role = key
    return _current_role


# --- suggestion (never auto-applied) -----------------------------------------

# (role, keywords) — first match wins, scanned in this order.
_SUGGEST_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("debugger", ("why is", "why does", "failing", "failure", "traceback", "stack trace",
                  "crash", "broken", "error:", "exception", "root cause", "not working")),
    ("reviewer", ("review", "code review", "look over my", "pr ", "pull request",
                  "any bugs", "security issue", "vulnerab")),
    ("tester", ("test", "add tests", "unit test", "coverage", "verify it", "make it pass")),
    ("architect", ("design", "architecture", "how should i structure", "approach for",
                   "plan out", "structure the")),
    ("implementer", ("add ", "implement", "build ", "create ", "write a", "refactor",
                     "feature")),
    ("researcher", ("how does", "explain", "understand", "where is", "what does",
                    "trace through", "walk me through")),
]


def suggest_role(task: str) -> str | None:
    """Heuristic best-fit role for a task, or None. Pure; purely a *suggestion* —
    callers surface it for the user, never switch roles automatically."""
    low = (task or "").lower()
    for role, keywords in _SUGGEST_RULES:
        if any(k in low for k in keywords):
            return role
    return None
