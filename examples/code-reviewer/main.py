"""End-to-end code-reviewer agent built on Ronin.

Demonstrates the Multi-Agent Supervisor pattern with output validation:

- Three specialist sub-agents review the file in parallel (style / bugs / security).
- The orchestrator synthesizes their findings.
- Output is validated against a ``CodeReview`` Pydantic schema before returning.

Run:
    export ANTHROPIC_API_KEY=sk-ant-...
    uv run python examples/code-reviewer/main.py examples/code-reviewer/sample_buggy_code.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ronin_agent_patterns import (
    AnthropicProvider,
    LLMProvider,
    SubAgent,
    SupervisorAgent,
)


class Finding(BaseModel):
    severity: Literal["info", "low", "medium", "high", "critical"]
    line: int | None = None
    title: str
    explanation: str
    suggestion: str | None = None
    category: Literal["style", "bug", "security"]


class CodeReview(BaseModel):
    summary: str = Field(description="One paragraph overall verdict.")
    findings: list[Finding] = Field(default_factory=list)
    overall_severity: Literal["clean", "minor", "needs_work", "block"]


REVIEWER_SYSTEMS = {
    "style_reviewer": (
        "You review code for style and readability. Flag: unclear names, missing type hints, "
        "long functions, magic numbers, dead code, missing docstrings on public APIs. "
        "Stay in scope — do NOT comment on bugs or security."
    ),
    "bug_finder": (
        "You hunt for runtime bugs and logic errors. Flag: off-by-one, missing error handling, "
        "uncaught edge cases, type mismatches, division-by-zero, nil-deref, race conditions. "
        "Stay in scope — do NOT comment on style or security."
    ),
    "security_auditor": (
        "You audit for security issues. Flag: SQL injection, command injection, path traversal, "
        "hardcoded secrets, missing auth checks, XSS, CSRF. Stay in scope — do NOT comment on "
        "style or non-security bugs."
    ),
}


def build_supervisor(provider: LLMProvider) -> SupervisorAgent:
    sub_agents = [
        SubAgent(
            name=name,
            description=desc,
            system=system,
            provider=provider,
        )
        for name, (desc, system) in {
            "style_reviewer": ("Reviews code for style and readability issues.", REVIEWER_SYSTEMS["style_reviewer"]),
            "bug_finder": ("Reviews code for bugs and logic errors.", REVIEWER_SYSTEMS["bug_finder"]),
            "security_auditor": ("Audits code for security vulnerabilities.", REVIEWER_SYSTEMS["security_auditor"]),
        }.items()
    ]

    schema = json.dumps(CodeReview.model_json_schema(), indent=2)
    supervisor_system = (
        "You are a senior reviewer aggregating specialist findings. For each file:\n"
        "1. Delegate to style_reviewer, bug_finder, security_auditor — pass them the full file.\n"
        "2. Aggregate their findings, deduplicating overlapping reports.\n"
        "3. Assign overall_severity: clean (no findings), minor (only style/info), "
        "needs_work (medium bugs or low security), block (any high/critical).\n\n"
        f"Output the aggregate review as a single JSON object matching this schema, "
        f"wrapped in <review></review> tags:\n{schema}"
    )

    return SupervisorAgent(
        system=supervisor_system,
        sub_agents=sub_agents,
        provider=provider,
        max_iterations=10,
    )


def review_file(path: Path, provider: LLMProvider) -> tuple[CodeReview, list]:
    code = path.read_text(encoding="utf-8")
    user_message = (
        f"Review this file: {path.name}\n\n"
        f"```python\n{code}\n```\n\n"
        "Delegate to all three specialists, then return the aggregate CodeReview."
    )
    supervisor = build_supervisor(provider)
    result = supervisor.run(user_message)
    if not result.success:
        raise RuntimeError(f"agent run failed: {result.error}")

    match = re.search(r"<review>(.*?)</review>", result.output, re.DOTALL)
    if not match:
        match = re.search(r"(\{.*\})", result.output, re.DOTALL)
        if not match:
            raise ValueError(f"no CodeReview JSON found in: {result.output[:300]}")
    return CodeReview.model_validate_json(match.group(1).strip()), result.trace


SEVERITY_GLYPH = {"info": "ℹ️ ", "low": "💡", "medium": "⚠️ ", "high": "🚨", "critical": "🔥"}


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python main.py <path/to/file.py>")
        sys.exit(1)

    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — this example needs a real Claude key.")
        sys.exit(2)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"file not found: {path}")
        sys.exit(1)

    review, trace = review_file(path, AnthropicProvider())

    print("=" * 70)
    print(f"REVIEW: {path}")
    print(f"OVERALL: {review.overall_severity.upper()}")
    print(f"SUMMARY: {review.summary}")
    print("=" * 70)
    if not review.findings:
        print("(no findings)")
        return

    by_category: dict[str, list[Finding]] = {"security": [], "bug": [], "style": []}
    for f in review.findings:
        by_category[f.category].append(f)

    for cat, findings in by_category.items():
        if not findings:
            continue
        print(f"\n[{cat.upper()}] ({len(findings)})")
        for f in findings:
            line_str = f"L{f.line} " if f.line else ""
            print(f"  {SEVERITY_GLYPH.get(f.severity, '·')} {line_str}{f.title}")
            print(f"     {f.explanation}")
            if f.suggestion:
                print(f"     → {f.suggestion}")

    print(f"\n[{len(trace)} trace steps]")


if __name__ == "__main__":
    main()
