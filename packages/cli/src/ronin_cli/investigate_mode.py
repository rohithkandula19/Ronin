"""``ronin investigate`` — the unique wedge: bridge business signals and code.

Claude Code knows your *code* but nothing about your *business*. A BI tool
knows your *metrics* but can't read your *code*. Neither connects the two.

`ronin investigate "<symptom>"` does. Given a business symptom — "failed
payments spiked", "churn jumped", "signups dropped" — it:

1. Pulls the ops data (Stripe / Linear / …) to quantify and timestamp it.
2. Searches the codebase for the module that owns the affected area.
3. Uses git history to find recent changes near that time.
4. Connects them: "the failed-charge spike on the 9th lines up with the deploy
   that changed stripe_webhook.py (commit abc, also the 9th)."

Read-only by design: ops tools are read-only, code tools are limited to
read/list/search + git via run_command (gated). No write_file / edit_file —
investigation never mutates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from ronin_agent_patterns import ReActAgent, Step, Tool
from ronin_hardening import InjectionScanner

from .agent_mode import _narrate
from .code_tools import build_code_tools
from .config import RoninConfig
from .runner import build_provider


INVESTIGATE_SYSTEM = """You are ronin in investigate mode — a root-cause analyst that bridges \
business signals and code.

You are given a SYMPTOM (e.g. "failed payments spiked", "churn jumped this week",
"signups dropped"). Find the likely cause by looking at BOTH sides:

- BUSINESS DATA (Stripe, Linear, etc.): quantify the symptom and pin down WHEN it
  started. Which customers, how big, what changed week-over-week.
- CODE (read files, search, git log/blame via run_command): find the module that
  owns the affected behavior and any recent changes near the symptom's timeframe.

Then CONNECT them with evidence from both: e.g. "failed charges jumped from 1/wk
to 14 starting the 9th; git log shows stripe_webhook.py was changed in commit
a1b2c3 on the 9th — likely cause."

Rules:
- You are READ-ONLY. You can read data, read code, and run read-only git
  commands (git log, git blame, git diff). You cannot modify anything.
- If the data and code don't connect, say so and state what you'd need to confirm.
- Cite specific numbers, file paths, and commit hashes. No hand-waving.
"""

# Read-only code tools for investigation: drop the mutating ones.
_INVESTIGATE_CODE_TOOLS = {"read_file", "list_files", "search_files", "run_command"}
# Only run_command is gated here (for git); reads run freely.
_INVESTIGATE_SENSITIVE = {"run_command"}


@dataclass
class InvestigateResult:
    success: bool
    output: str
    iterations: int
    steps: list[Step] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    blocked: bool = False


def build_investigate_tools(config: RoninConfig, root: Path | str) -> list[Tool]:
    """Union of read-only ops tools + read-only code tools."""
    from .tools import build_tools

    ops = build_tools(config)
    code = [t for t in build_code_tools(root) if t.name in _INVESTIGATE_CODE_TOOLS]
    return ops + code


def _gate(console: Console | None, yolo: bool):
    from .approvals import is_floored_tool_call

    def gate(name: str, args: dict) -> bool:
        # Destructive floor FIRST — before the "not sensitive → run freely"
        # short-circuit, exactly as in code_mode's two gates. Checking sensitivity
        # first meant the floor was only ever reached by tools already CLASSIFIED
        # sensitive, so any other tool (an MCP `query` carrying DROP TABLE, a
        # plugin tool carrying rm -rf) skipped it entirely. A classification lookup
        # does not get to decide whether the outermost safety authority runs.
        floored = is_floored_tool_call(name, args)
        if not floored and name not in _INVESTIGATE_SENSITIVE:
            return True
        # A catastrophic payload is NEVER auto-approved under --yolo/god-mode: it
        # always drops to an interactive human confirmation (headless denies).
        if yolo and not floored:
            return True
        if console is None:
            return False
        label = "[red]destructive[/red] " if floored else ""
        console.print(f"[yellow]?[/yellow] run {label}[cyan]{args.get('command')}[/cyan]? [y/N] ", end="")
        try:
            return input().strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    return gate


def run_investigate(
    config: RoninConfig,
    symptom: str,
    *,
    root: Path | str = ".",
    console: Console | None = None,
    yolo: bool = False,
    max_iterations: int = 20,
) -> InvestigateResult:
    scan = InjectionScanner().scan(symptom)
    if scan.flagged:
        return InvestigateResult(
            success=False,
            output="[blocked] your input was flagged as a potential prompt-injection attempt.",
            iterations=0,
            error=f"injection-scan flagged: {[h['label'] for h in scan.hits]}",
            blocked=True,
        )

    tools = build_investigate_tools(config, root)
    services = ", ".join(config.configured_services()) or "none"
    agent = ReActAgent(
        system=INVESTIGATE_SYSTEM + f"\n\nConfigured data services: {services}. Code root: {Path(root).resolve()}.",
        tools=tools,
        provider=build_provider(config),
        max_iterations=max_iterations,
        compact_after_tokens=120_000,
        compact_keep_recent=6,
    )

    on_step = _narrate(console) if console is not None else None
    before_tool = _gate(console, yolo)

    result = agent.run(symptom, on_step=on_step, before_tool=before_tool)
    return InvestigateResult(
        success=result.success,
        output=result.output,
        iterations=result.iterations,
        steps=result.trace,
        usage=result.usage,
        error=result.error,
    )
