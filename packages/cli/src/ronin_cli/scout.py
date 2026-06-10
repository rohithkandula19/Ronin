"""Scout → Strike — explore cheap, strike strong.

Most of a coding task is *reading*: mapping the repo, finding the right files,
forming a plan. That work doesn't need a frontier model. Scout → Strike sends the
read-only reconnaissance to a cheap/free blade (Cerebras, Groq, Gemini), then
hands the plan to a strong blade that only does the actual edits. You get the
quality of the strong model where it matters and pay for it on a fraction of the
turn — a split only a provider-agnostic agent can make.

The scout/strike blades come from your routing config: ``route_fast`` is the
scout, ``route_strong`` is the strike (each may be a provider-qualified
``provider:model``). The blade resolver is pure and unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console

from .config import RoninConfig


@dataclass
class Blades:
    scout: RoninConfig    # cheap/free, read-only reconnaissance
    strike: RoninConfig   # strong, does the edits
    scout_label: str
    strike_label: str


def resolve_blades(config: RoninConfig) -> Blades | None:
    """Derive the (scout, strike) configs from routing targets, or ``None`` when
    routing isn't configured (no ``route_fast``/``route_strong``)."""
    if not (config.route_fast and config.route_strong):
        return None
    from .routing import _parse_target
    from .runner import config_for_spec

    fp, fm = _parse_target(config.route_fast, config.provider)
    sp, sm = _parse_target(config.route_strong, config.provider)
    scout = config_for_spec(config, {"provider": fp, "model": fm})
    strike = config_for_spec(config, {"provider": sp, "model": sm})
    return Blades(
        scout=scout, strike=strike,
        scout_label=f"{scout.provider}:{scout.resolved_model()}",
        strike_label=f"{strike.provider}:{strike.resolved_model()}",
    )


def run_scout_strike(config: RoninConfig, task: str, root, console: Console, *,
                     max_steps: int = 25, yolo: bool = False):
    """Two-phase run: free scout drafts a plan (read-only), strong strike executes.

    Falls back to a normal single-model run if routing isn't configured. Returns
    the strike phase's AgentResult."""
    from .code_mode import run_code_agent

    blades = resolve_blades(config)
    if blades is None:
        console.print("[dim]scout→strike needs routing — set route_fast / route_strong. "
                      "Running single-model.[/dim]")
        return run_code_agent(config, task, root=root, console=console, yolo=yolo,
                              max_iterations=max_steps)

    console.print(f"[bold #7dcfff]🔭 scout[/bold #7dcfff] · [dim]{blades.scout_label} "
                  "(free/cheap) — read-only recon[/dim]")
    plan = run_code_agent(
        blades.scout,
        f"Produce a concise, step-by-step PLAN to accomplish: {task}\n"
        "Explore with read-only tools (read/search/glob). Name the exact files to "
        "change and what to change in each. DO NOT edit anything — just the plan.",
        root=root, console=console, yolo=True, max_iterations=max_steps, read_only=True,
    )

    console.print(f"[bold #bb9af7]⚔ strike[/bold #bb9af7] · [dim]{blades.strike_label} "
                  "(strong) — executing the plan[/dim]")
    return run_code_agent(
        blades.strike,
        f"Follow this plan (from a recon pass):\n{plan.output}\n\nNow implement it: {task}",
        root=root, console=console, yolo=yolo, max_iterations=max_steps,
    )
