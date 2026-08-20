"""``ronin explain <path>`` — the onboarding killer feature.

Point it at an unfamiliar file, module, or repo and ronin makes it make sense:

1. a plain-English explanation (big picture → key pieces → data flow),
2. an auto-generated **Mermaid architecture diagram** (renders on GitHub /
   pastes straight into a README),
3. optional **voice narration** (``--speak``).

A pure coding agent *explains*. ronin explains, **draws it**, and **speaks it** —
because it has a diagram generator and a voice. Read-only: it explores with
read_file / list_files / search_files and never mutates anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from ronin_agent_patterns import ReActAgent, Step
from ronin_hardening import InjectionScanner

from .code_tools import build_code_tools
from .config import RoninConfig
from .runner import build_provider
from .streaming import LiveRenderer

_READONLY_TOOLS = {"read_file", "list_files", "search_files"}

EXPLAIN_SYSTEM = """You are ronin in explain mode — you make an unfamiliar codebase clear to a \
competent engineer who is new to THIS code.

Explore the target path with your read-only tools (list_files, read_file, search_files), then explain:
- THE BIG PICTURE first: what this code is for and why it exists.
- The key files / functions / classes and how they fit together.
- The main data/control flow — trace the important path end to end.
Be concrete: reference real names, files, and line-level behavior. Be concise; no filler.
{diagram_clause}"""

_DIAGRAM_CLAUSE = """
Then, after the prose explanation, output ONE architecture/flow diagram as a Mermaid diagram
inside a fenced code block exactly like:
```mermaid
flowchart TD
  A[entrypoint] --> B[...]
```
Keep it to the important nodes and edges (aim for 5-12 nodes). Use real component names."""

_MERMAID_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class ExplainResult:
    success: bool
    output: str
    mermaid: str | None = None
    iterations: int = 0
    steps: list[Step] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    blocked: bool = False
    streamed: bool = False


def extract_mermaid(text: str) -> str | None:
    """Pull the first ```mermaid ...``` block out of ``text`` (without fences)."""
    m = _MERMAID_RE.search(text or "")
    return m.group(1).strip() if m else None


def strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks — used to get clean prose for text-to-speech."""
    return re.sub(r"```.*?```", "", text or "", flags=re.DOTALL).strip()


def run_explain(
    config: RoninConfig,
    target: str,
    question: str | None = None,
    *,
    root: Path | str = ".",
    diagram: bool = True,
    console: Console | None = None,
    max_iterations: int = 15,
) -> ExplainResult:
    probe = f"{target} {question or ''}"
    scan = InjectionScanner().scan(probe)
    if scan.flagged:
        return ExplainResult(
            success=False,
            output="[blocked] your input was flagged as a potential prompt-injection attempt.",
            error=f"injection-scan flagged: {[h['label'] for h in scan.hits]}",
            blocked=True,
        )

    tools = [t for t in build_code_tools(root) if t.name in _READONLY_TOOLS]
    system = EXPLAIN_SYSTEM.format(diagram_clause=_DIAGRAM_CLAUSE if diagram else "")
    from .context_policy import resolve_context_policy

    context_policy = resolve_context_policy(config)
    agent = ReActAgent(
        system=system,
        tools=tools,
        provider=build_provider(config),
        max_iterations=max_iterations,
        compact_after_tokens=context_policy.compaction_threshold_tokens,
        compact_keep_recent=6,
    )

    ask = f"Explain this code: {target}."
    if question:
        ask += f" Focus: {question}"

    renderer = LiveRenderer(console) if console is not None else None
    on_step = renderer.on_step if renderer is not None else None
    on_text = renderer.on_text if renderer is not None else None
    on_reset = renderer.on_reset if renderer is not None else None

    result = agent.run(ask, on_step=on_step, on_text=on_text, on_reset=on_reset)
    if renderer is not None:
        renderer.finish()

    return ExplainResult(
        success=result.success,
        output=result.output,
        mermaid=extract_mermaid(result.output) if diagram else None,
        iterations=result.iterations,
        steps=result.trace,
        usage=result.usage,
        error=result.error,
        streamed=bool(renderer and renderer.streamed_text),
    )
