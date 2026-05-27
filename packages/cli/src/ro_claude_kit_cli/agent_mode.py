"""``csk agent`` — autonomous multi-step agent with live narration + approval gates.

This is the Cline-shaped surface: you give it a goal, it reasons and chains
tool calls on its own, narrating each step as it happens, and (optionally)
pausing for your approval before each tool call.

`csk ask` is one-shot. `csk chat` is turn-by-turn. `csk agent` is the
autonomous-until-done loop with a higher iteration budget and live step output.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from rich.console import Console
from rich.panel import Panel

from ro_claude_kit_agent_patterns import ReActAgent, Step, Tool
from ro_claude_kit_hardening import InjectionScanner

from .config import CSKConfig
from .runner import build_provider
from .tools import build_tools


AGENT_SYSTEM = """You are ronin in agent mode — an autonomous operations analyst for a startup founder.

You are given a GOAL. Pursue it by calling the read-only tools available to you,
one reasoning step at a time, until you can give a complete, specific answer.

Rules:
- Prefer specific tool filters over listing everything.
- Chain tools: use the output of one call to inform the next.
- When you have enough information, stop and give a tight, sourced answer.
- You have only read access. If the goal requires changing data, say so and stop.
- Cite the concrete numbers / records you used.
"""


@dataclass
class AgentRunResult:
    success: bool
    output: str
    iterations: int
    steps: list[Step] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    blocked: bool = False
    streamed: bool = False  # True if the answer already streamed to the console


def _narrate(console: Console) -> Callable[[Step], None]:
    """Build an on_step callback that prints each step live with Rich."""
    icon = {
        "thought": "[dim]🤔[/dim]",
        "tool_call": "[cyan]🔧[/cyan]",
        "tool_result": "[green]📥[/green]",
        "plan": "[#2dd4bf]🗂[/#2dd4bf]",
        "reflection": "[yellow]🔎[/yellow]",
        "final": "[bold green]✅[/bold green]",
        "error": "[red]⚠️[/red]",
    }

    def on_step(step: Step) -> None:
        tag = icon.get(step.kind, "·")
        content = step.content
        if step.kind == "tool_call" and isinstance(content, dict):
            console.print(f"{tag} [cyan]{content.get('name')}[/cyan]([dim]{content.get('input')}[/dim])")
        elif step.kind == "tool_result" and isinstance(content, dict):
            preview = str(content.get("result", ""))[:160]
            console.print(f"{tag} [dim]{preview}[/dim]")
        elif step.kind == "final":
            return  # printed separately by the caller in a panel
        else:
            console.print(f"{tag} [dim]{str(content)[:200]}[/dim]")

    return on_step


def _approval_gate(console: Console, auto: bool) -> Callable[[str, dict], bool]:
    """Build a before_tool gate. In ``auto`` mode everything is approved.

    Otherwise, pause and ask y/n before each tool call (Cline-style). Since
    every csk tool is read-only, the default is auto; --confirm flips it on.
    """
    def gate(name: str, args: dict) -> bool:
        if auto:
            return True
        console.print(f"[yellow]?[/yellow] run [cyan]{name}[/cyan]([dim]{args}[/dim])? [y/N] ", end="")
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")

    return gate


def run_agent(
    config: CSKConfig,
    goal: str,
    *,
    console: Console | None = None,
    confirm: bool = False,
    max_iterations: int = 15,
) -> AgentRunResult:
    """Run the autonomous agent loop toward ``goal``.

    Requires a real provider key — agent mode does not fall back to the offline
    demo brain (an autonomous multi-step loop is meaningless without a real LLM).
    """
    scan = InjectionScanner().scan(goal)
    if scan.flagged:
        return AgentRunResult(
            success=False,
            output="[blocked] your goal was flagged as a potential prompt-injection attempt.",
            iterations=0,
            error=f"injection-scan flagged: {[h['label'] for h in scan.hits]}",
            blocked=True,
        )

    tools: list[Tool] = build_tools(config)
    services = ", ".join(config.configured_services()) or "none"
    system = AGENT_SYSTEM + f"\n\nConfigured services: {services}."

    agent = ReActAgent(
        system=system,
        tools=tools,
        provider=build_provider(config),
        max_iterations=max_iterations,
    )

    before_tool = _approval_gate(console, auto=not confirm) if console is not None else None

    # Stream tokens live when we have a console (Claude-Code feel).
    from .streaming import LiveRenderer
    renderer = LiveRenderer(console) if console is not None else None
    on_step = renderer.on_step if renderer is not None else None
    on_text = renderer.on_text if renderer is not None else None

    result = agent.run(goal, on_step=on_step, before_tool=before_tool, on_text=on_text)
    if renderer is not None:
        renderer.finish()
    return AgentRunResult(
        success=result.success,
        output=result.output,
        iterations=result.iterations,
        steps=result.trace,
        usage=result.usage,
        error=result.error,
        streamed=bool(renderer and renderer.streamed_text),
    )


def has_real_key(config: CSKConfig) -> bool:
    if config.provider == "anthropic":
        return bool(config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"))
    if config.provider == "ollama":
        return True
    return bool(config.openai_api_key or os.environ.get("OPENAI_API_KEY"))
