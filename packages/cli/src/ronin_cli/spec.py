"""Design-doc-first — `ronin spec "<feature>"`.

Before writing code, ronin explores the repo (read-only) and writes a concise,
concrete design spec: the problem, the approach, the exact files to change, edge
cases, a test plan, and open questions. Think before you build — and have the
plan grounded in the actual codebase. The prompt/section logic is pure and
unit-tested; the spec itself is produced by the agent.
"""
from __future__ import annotations

SPEC_SECTIONS = [
    "Problem / goal",
    "Proposed approach",
    "Files to change",
    "Edge cases & risks",
    "Test plan",
    "Open questions",
]


def spec_prompt(feature: str, sections: list[str] | None = None) -> str:
    """Build the design-spec prompt for ``feature``. Pure."""
    secs = sections or SPEC_SECTIONS
    body = "\n".join(f"## {s}" for s in secs)
    return (
        f"Write a concise DESIGN SPEC for this feature/change:\n\n{feature}\n\n"
        "First explore the codebase with read-only tools (read/search/glob) so the "
        "plan is grounded in how this repo actually works. Then output the spec as "
        "markdown with EXACTLY these sections — be specific: name real files, "
        "functions, and call sites; list concrete edge cases; propose actual tests.\n\n"
        f"{body}\n"
    )
