"""ronin — Claude-powered CLI for startup ops.

Subcommands:
    ronin init              — create a config file (interactive or --demo).
    ronin ask "<question>"  — one-shot agent run against your configured tools.
    ronin chat              — interactive REPL (multi-turn with short-term memory).
    ronin tools             — list the tools registered for the current config.
    ronin doctor            — health check.
    ronin save NAME "Q"     — save a question for later recall.
    ronin run NAME          — run a saved question.
    ronin queries           — list saved questions.
    ronin eval ...          — eval suite (golden datasets, drift detection).
    ronin version           — print version.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import __version__
from .config import PROVIDER_PRESETS, RoninConfig, find_config_path, load_config, save_config
from .runner import AgentResultRich, run_ask, start_chat
from .saved_queries import QueryStore, default_path as queries_path
from .tools import build_tools

app = typer.Typer(
    name="ronin",
    help="ronin — a masterless Claude agent CLI. Ops briefings, autonomous data questions, and a coding agent.",
    no_args_is_help=False,
    add_completion=False,
)
console = Console()


# The panda mascot (dancing / running / playing / playing football / sleeping)
# lives in panda_art.py — single source of truth for both the launch banner
# and the `ronin panda` command. We re-export the activities dict so existing
# imports keep working.
from .panda_art import PANDA_ACTIVITIES as _PANDA_ACTIVITIES, render_panda as _render_panda


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    tui: bool = typer.Option(
        False, "--tui",
        help="Open the full-screen TUI (panes + trace) instead of the inline REPL.",
    ),
    no_tui: bool = typer.Option(
        False, "--no-tui", "--repl", hidden=True,
        help="(default) the inline REPL — kept for backward-compatibility.",
    ),
    offline: bool = typer.Option(
        False, "--offline",
        help="Zero network egress: forces a local brain (Ollama) and strips all "
             "network tools. Nothing leaves the machine.",
    ),
    full_access: bool = typer.Option(
        False, "--full-access", "--god-mode",
        help="Lift all guards: filesystem-wide access (beyond the project root), "
             "auto-approve every edit/command (no y/n), longer timeouts. Powerful "
             "and unsandboxed — only in a directory you trust.",
    ),
    sentinel: bool = typer.Option(
        False, "--sentinel",
        help="Abstain over bluff: the agent declares CONFIDENCE: high/medium/low and "
             "says what it's unsure about. Low confidence on a cheap blade escalates "
             "to the strong one (with routing).",
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return

    # When _root is called directly (not via typer's CLI parsing — e.g. from
    # tests), typer leaves parameter defaults as OptionInfo objects, not real
    # bools. Normalise so direct callers see False by default.
    if not isinstance(tui, bool):
        tui = False
    if not isinstance(no_tui, bool):
        no_tui = False
    if not isinstance(offline, bool):
        offline = False
    if not isinstance(full_access, bool):
        full_access = False

    import sys
    # Non-interactive (pipe/test) → just show help and exit, cleanly (no banner).
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        console.print(ctx.get_help())
        return

    config = load_config()
    if offline:
        from .offline import apply_offline
        config = apply_offline(config.model_copy(update={"offline": True}))
        console.print(f"[dim]🔒 offline mode — local brain ([bold]{config.provider}[/bold]), "
                      "no network tools, nothing leaves this machine[/dim]")
    if not offline and not config.has_provider_auth():
        from .theme import ACCENT
        console.print(f"\n[bold {ACCENT}]ʕ•ᴥ•ʔ  ronin[/bold {ACCENT}]\n")
        console.print(
            "[dim]No provider configured yet. Run [bold]ronin init[/bold] "
            "(or [bold]ronin init --demo[/bold]) to get started, or [bold]ronin --help[/bold] for all commands.[/dim]"
        )
        console.print(ctx.get_help())
        return

    if full_access:
        config = config.model_copy(update={"full_access": True})
        console.print("[bold #e0af68]⚠ FULL-ACCESS MODE[/bold #e0af68] [dim]— filesystem-wide, "
                      "auto-approving every edit & command, no sandbox. Use only in a directory "
                      "you trust.[/dim]")
    if not isinstance(sentinel, bool):
        sentinel = False
    if sentinel:
        config = config.model_copy(update={"sentinel": True})
        console.print("[#6b7089]🛡 sentinel mode — the agent abstains over bluffing[/#6b7089]")

    root = Path(".")

    # Default: the minimal, Claude-Code-style INLINE REPL — output flows in the
    # terminal's normal scrollback under a tiny logo and a bordered input box (no
    # full-screen takeover, no side panes). `ronin --tui` opts into the
    # full-screen Textual app (panes + live trace + approval modal) for those who
    # want it.
    if tui and not no_tui:
        from .tui import run_tui
        run_tui(config=config, root=str(root))
        return

    from .code_mode import run_code_session, run_unified_session

    if _is_code_project(root):
        if not full_access:
            console.print("[dim]code project · reads run free, edits & commands need approval[/dim]")
        run_code_session(config, root=root, console=console, yolo=full_access)
        return

    # Outside a code repo = one assistant that does everything: talk, generate
    # media, query data, write/run code when asked.
    run_unified_session(config, root=root, console=console, yolo=full_access)


def _is_code_project(path: Path) -> bool:
    """Heuristic: does ``path`` look like a code repository? Used to greet you
    with a code-aware hint when you launch bare ``ronin`` inside a repo."""
    markers = (
        ".git", "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
        "pom.xml", "build.gradle", "Gemfile", "RONIN.md", "CLAUDE.md", "AGENTS.md",
    )
    return any((path / m).exists() for m in markers)


# ---------- init ----------

@app.command()
def init(
    demo: bool = typer.Option(False, "--demo", help="Skip credential prompts; use built-in demo data."),
    scope: str = typer.Option("project", "--scope", help="'project' (./.ronin/) or 'user' (~/.config/csk/)."),
    yes: bool = typer.Option(False, "-y", "--yes", help="Overwrite existing config without confirmation."),
) -> None:
    """Create a ronin config file."""
    existing = find_config_path()
    if existing and not yes:
        if not Confirm.ask(f"[yellow]A config already exists at[/yellow] {existing}. Overwrite?", default=False):
            console.print("[dim]aborted[/dim]")
            raise typer.Exit(1)

    if demo:
        cfg = RoninConfig(demo_mode=True)
        path = save_config(cfg, scope=scope)
        console.print(Panel.fit(
            f"[green]✓[/green] Demo config written to [cyan]{path}[/cyan]\n\n"
            "Try it now:\n"
            "  [bold]ronin ask[/bold] [cyan]\"how many active subscriptions do we have?\"[/cyan]\n"
            "  [bold]ronin tools[/bold]",
            title="Ready", border_style="green",
        ))
        return

    console.print(Panel.fit(
        "[bold]ronin init[/bold] — pick an LLM provider, then add credentials for the services "
        "you want it to query.\n[dim]Values are stored in plaintext at .ronin/config.toml — .gitignore "
        "that path.[/dim]",
        border_style="cyan",
    ))

    provider_choice = Prompt.ask(
        "LLM provider",
        choices=["anthropic", "ollama", "openai", "together", "groq", "fireworks", "custom"],
        default="anthropic",
    )
    preset = PROVIDER_PRESETS.get(provider_choice, {})
    default_model = preset.get("model") or ""
    model = Prompt.ask("Model", default=default_model)
    # Guard: people sometimes answer the Model prompt with "yes"/"y" (as if it
    # were a confirmation), which silently saves an invalid model and 404s at
    # runtime. Reject those and fall back to the provider's default.
    if model.strip().lower() in {"y", "n", "yes", "no", "true", "false", "ok"} and default_model:
        console.print(
            f"[yellow]'{model}' isn't a model name[/yellow] — using the default "
            f"[bold]{default_model}[/bold] instead. (Pass --model or edit .ronin/config.toml to change it.)"
        )
        model = default_model
    base_url: str | None = None
    if provider_choice == "custom":
        base_url = Prompt.ask("OpenAI-compatible base URL")
    elif provider_choice not in ("anthropic", "ollama"):
        base_url = preset.get("base_url") or ""

    anthropic_key: str | None = None
    openai_key: str | None = None
    if provider_choice == "anthropic":
        anthropic_key = Prompt.ask(
            "ANTHROPIC_API_KEY",
            default=os.environ.get("ANTHROPIC_API_KEY") or "",
            password=True,
        ) or None
    elif provider_choice != "ollama":
        openai_key = Prompt.ask(
            f"API key for {provider_choice} (your {provider_choice} key; input is hidden — paste ONCE)",
            default=os.environ.get("OPENAI_API_KEY") or "",
            password=True,
        ) or None

    # The key prompt hides input, so confirm what landed (length + masked preview)
    # — this is what stops the "did my paste work?" double-paste problem.
    chosen_key = anthropic_key or openai_key
    if chosen_key:
        console.print(f"[dim]key received:[/dim] {_key_preview(chosen_key)}")

    console.print()
    stripe = Prompt.ask("Stripe API key (rk_live_... recommended)", default="", password=True)
    linear = Prompt.ask("Linear API key", default="", password=True)
    slack_bot = Prompt.ask("Slack bot token (xoxb-...)", default="", password=True)
    notion = Prompt.ask("Notion integration token", default="", password=True)
    database_url = Prompt.ask("Postgres DATABASE_URL", default="")

    cfg = RoninConfig(
        provider=provider_choice,
        model=model or None,
        base_url=base_url or None,
        anthropic_api_key=anthropic_key,
        openai_api_key=openai_key,
        stripe_api_key=stripe or None,
        linear_api_key=linear or None,
        slack_bot_token=slack_bot or None,
        notion_token=notion or None,
        database_url=database_url or None,
    )
    path = save_config(cfg, scope=scope)
    console.print(f"\n[green]✓[/green] Wrote config to [cyan]{path}[/cyan]")
    services = cfg.configured_services()
    console.print(f"[dim]provider:[/dim] {provider_choice} ({cfg.resolved_model()})")
    console.print(f"[dim]configured services:[/dim] {', '.join(services) if services else '[red]none[/red]'}")
    console.print("[dim]verify with [bold]ronin doctor --check[/bold][/dim]")


def _key_preview(key: str) -> str:
    """A safe, non-revealing confirmation of a pasted key: length + masked ends."""
    n = len(key)
    if n == 0:
        return "[dim](empty)[/dim]"
    masked = f"{key[:4]}…{key[-4:]}" if n > 10 else "…"
    if 30 < n < 80:
        flag = "[green]looks right ✅[/green]"
    elif n >= 80:
        flag = "[red]too long — looks double-pasted ❌[/red]"
    else:
        flag = "[yellow]short — double-check ⚠️[/yellow]"
    return f"{n} chars · {masked} · {flag}"


@app.command("set-key")
def set_key(
    provider: str = typer.Option(None, "--provider", help="Set/override the provider (e.g. groq, openrouter, anthropic)."),
    model: str = typer.Option(None, "--model", help="Set/override the model (e.g. qwen/qwen3-coder:free)."),
    scope: str = typer.Option("project", "--scope", help="'project' (./.ronin/) or 'user' (~/.config/csk/)."),
) -> None:
    """Set just the LLM API key — masked input with a length/preview confirmation
    so you can tell the paste actually worked (no more blind double-pasting)."""
    config = load_config()
    if provider:
        config.provider = provider
    if model:
        config.model = model
    prov = config.provider
    if prov == "ollama":
        console.print("[yellow]ollama runs locally and needs no key.[/yellow]")
        raise typer.Exit(0)

    key = (Prompt.ask(f"API key for {prov} (input hidden — paste ONCE, then Enter)", password=True) or "").strip()
    if not key:
        console.print("[yellow]no key entered — nothing changed[/yellow]")
        raise typer.Exit(1)
    console.print(f"[dim]received:[/dim] {_key_preview(key)}")
    if len(key) >= 80:
        console.print("[red]✗ that looks like the key pasted multiple times — run [bold]ronin set-key[/bold] again and paste ONCE.[/red]")
        raise typer.Exit(1)

    config.demo_mode = False
    config.set_key_for(prov, key)  # per-provider — never clobbers another provider's key
    path = save_config(config, scope=scope)
    console.print(f"[green]✓[/green] key saved for [bold]{prov}[/bold] "
                  f"[dim]({config.resolved_model()})[/dim] → [cyan]{path}[/cyan]")
    console.print("[dim]verify it works: [bold]ronin doctor --check[/bold][/dim]")


# ---------- ask ----------

@app.command()
def ask(
    question: list[str] = typer.Argument(None, help="The question to ask. Wrap multi-word questions in quotes."),
    raw: bool = typer.Option(False, "--raw", help="Print plain output instead of rich panels."),
) -> None:
    """One-shot Q&A — also reads piped stdin, so ronin composes in shell pipelines.

    Examples:
      ronin ask "which customers churned?"
      cat error.log | ronin ask "what's the root cause?"
      git diff | ronin ask "write release notes for these changes"
    """
    config = load_config()
    if not config.has_provider_auth():
        console.print(
            f"[red]✗[/red] No credentials for provider [bold]{config.provider}[/bold]. "
            "Run [bold]ronin init[/bold] (or [bold]ronin init --demo[/bold]) first."
        )
        raise typer.Exit(2)

    text = " ".join(question) if question else ""
    # Pipe support: fold piped stdin in as context (cat file | ronin ask "…").
    if not sys.stdin.isatty():
        try:
            piped = sys.stdin.read().strip()
        except Exception:  # noqa: BLE001
            piped = ""
        if piped:
            text = f"{text}\n\n--- piped input ---\n{piped}" if text else piped
    if not text.strip():
        console.print("[red]✗[/red] nothing to ask — give a question or pipe input "
                      "([dim]cat file | ronin ask \"...\"[/dim]).")
        raise typer.Exit(2)

    result = run_ask(config, text, console=console)
    _print_result(result, raw=raw)


# ---------- chat ----------

@app.command()
def chat(
    raw: bool = typer.Option(False, "--raw", help="Plain output."),
) -> None:
    """Multi-turn REPL with short-term memory. Type :q or Ctrl-D to exit."""
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for provider [bold]{config.provider}[/bold]. Run [bold]ronin init[/bold] first.")
        raise typer.Exit(2)

    console.print(Panel.fit(
        "[bold cyan]ronin chat[/bold cyan] — multi-turn. Type [bold]:q[/bold] or Ctrl-D to exit.",
        border_style="cyan",
    ))
    start_chat(config, console=console, raw=raw)


# ---------- consensus (multi-model) ----------

@app.command()
def consensus(
    task: str = typer.Argument(..., help="The question / task to put to the panel of models."),
    models: str = typer.Option(
        ..., "--models", "-m",
        help="Comma-separated provider[:model] specs, e.g. "
             "'anthropic,gemini,cerebras' or 'openrouter:deepseek/deepseek-v4-flash:free,openrouter:qwen/qwen3-coder:free'.",
    ),
    judge: Optional[str] = typer.Option(
        None, "--judge", help="provider[:model] to synthesize the answers (defaults to your current provider)."),
) -> None:
    """Ask SEVERAL models the same thing in parallel, then synthesize one
    cross-checked answer. Read-only — for design/review/decision questions.

    Something a single-vendor agent structurally can't do: run Claude AND Gemini
    AND a local model at once and reconcile them.
    """
    from .consensus import parse_model_spec, render_consensus, run_consensus

    config = load_config()
    specs = [parse_model_spec(s) for s in models.split(",") if s.strip()]
    if len(specs) < 2:
        console.print("[yellow]consensus needs at least 2 models[/yellow] — e.g. "
                      "[cyan]--models anthropic,gemini[/cyan]")
        raise typer.Exit(2)
    judge_spec = parse_model_spec(judge) if judge else None

    labels = ", ".join(f"{s['provider']}{':' + s['model'] if s.get('model') else ''}" for s in specs)
    console.print(f"[dim]polling {len(specs)} models in parallel — {labels}…[/dim]")
    with console.status("[dim] gathering answers + synthesizing…[/dim]", spinner="dots"):
        result = run_consensus(config, task, specs, judge_spec=judge_spec)
    render_consensus(console, result)


@app.command()
def ghost(
    root: Path = typer.Option(Path("."), "--root", help="Directory to watch."),
    test: bool = typer.Option(False, "--test", help="Run the test suite after each save and react."),
    watchful: bool = typer.Option(False, "--watchful", help="On a test failure, ask a model what likely broke."),
    duel: str = typer.Option(None, "--duel", help="On a test failure, have a RIVAL provider[:model] red-team the diff."),
    interval: float = typer.Option(1.5, "--interval", help="Poll interval (seconds)."),
) -> None:
    """👻 Ambient pair — the panda watches your saves and reacts. With --test it
    runs your suite on each save (🐼✨ green / 🐼💥 broken); --watchful has a model
    guess what broke; --duel gemini has a rival model red-team the breaking diff.
    Dependency-free; Ctrl+C to dismiss.
    """
    from .ghost import run_ghost

    config = load_config()
    duel_spec = None
    if duel:
        from .consensus import parse_model_spec
        duel_spec = parse_model_spec(duel)
    run_ghost(config, root, console, interval=interval, run_tests=test,
              watchful=watchful, duel_against=duel_spec)


@app.command()
def dojo(
    task: str = typer.Argument(..., help="The coding task all the models will fight over."),
    models: str = typer.Option(
        ..., "--models", "-m", help="Comma-separated provider[:model] fighters, e.g. 'anthropic,gemini,cerebras'."),
    judge: Optional[str] = typer.Option(None, "--judge", help="provider[:model] that scores the diffs."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-apply the winning diff."),
) -> None:
    """🥋 Rival models each attempt the SAME change in parallel isolated worktrees;
    a judge crowns the best diff, which you can apply. Claude vs Gemini vs ….

    Needs git (parallel worktree isolation). Only a provider-agnostic agent can
    pit multiple vendors against each other on the same code.
    """
    from .consensus import parse_model_spec
    from .dojo import run_dojo
    from .kaizen import _apply_diff, _print_diff

    config = load_config()
    specs = [parse_model_spec(s) for s in models.split(",") if s.strip()]
    if len(specs) < 2:
        console.print("[yellow]the dojo needs at least 2 fighters[/yellow] — e.g. [cyan]-m anthropic,gemini[/cyan]")
        raise typer.Exit(2)
    judge_spec = parse_model_spec(judge) if judge else None
    labels = ", ".join(f"{s['provider']}{':' + s['model'] if s.get('model') else ''}" for s in specs)
    console.print(f"[dim]🥋 {len(specs)} fighters entering the dojo — {labels}…[/dim]")
    with console.status("[dim] fighting in parallel worktrees + judging…[/dim]", spinner="dots"):
        try:
            result = run_dojo(config, task, specs, judge_spec=judge_spec)
        except ValueError as e:
            console.print(f"[yellow]{e}[/yellow]")
            raise typer.Exit(2)

    # tally this dojo into the persistent leaderboard
    from .leaderboard import load_leaderboard, save_leaderboard
    _lb = load_leaderboard(Path("."))
    _lb.record(result.winner.label if result.winner else None,
               [f.label for f in result.contenders])
    save_leaderboard(Path("."), _lb)

    for f in result.fighters:
        if f.changed:
            mark = "[green]★ winner[/green]" if result.winner is f else "[dim]·[/dim]"
            console.print(f"{mark} [bold]{f.label}[/bold] [dim]({len(f.diff.splitlines())} diff lines)[/dim]")
        else:
            why = f.error or "no changes"
            console.print(f"[dim]✗ {f.label} — {why[:80]}[/dim]")
    if result.winner is None:
        console.print("[yellow]no winning diff[/yellow]")
        raise typer.Exit(1)
    console.print(f"\n[bold]winning diff[/bold] [dim]({result.winner.label})[/dim]:")
    _print_diff(console, result.winner.diff)
    if not yes:
        if not Confirm.ask("[bold]apply the winning diff?[/bold]", default=False):
            console.print("[dim]aborted — your tree is untouched[/dim]")
            return
    ok = _apply_diff(Path(".").resolve(), result.winner.diff)
    console.print("[green]✓ applied[/green]" if ok else "[red]could not apply cleanly[/red]")


@app.command()
def duel(
    against: str = typer.Option(
        ..., "--against", "-a",
        help="provider[:model] of the RIVAL reviewer, e.g. 'gemini' or 'cerebras:gpt-oss-120b'."),
    staged: bool = typer.Option(False, "--staged", help="Review the staged diff instead of the working tree."),
) -> None:
    """⚔ Have a RIVAL model adversarially red-team your current git diff.

    The author model is a poor judge of its own code; a different provider hunts
    for bugs, edge cases, and regressions it can't see. Read-only, advisory.
    Something a single-vendor agent can't do: a real cross-vendor second opinion.
    """
    from .consensus import parse_model_spec
    from .duel import render_verdict, review_diff
    from .git_helper import _git

    config = load_config()
    if _git(".", "rev-parse", "--is-inside-work-tree").returncode != 0:
        console.print("[yellow]not a git repository[/yellow]")
        raise typer.Exit(2)
    args = ["diff", "--cached"] if staged else ["diff"]
    diff = _git(".", *args).stdout
    if not diff.strip():
        where = "staged" if staged else "working-tree"
        console.print(f"[dim]no {where} changes to duel over[/dim]")
        return
    spec = parse_model_spec(against)
    console.print(f"[dim]⚔ {spec['provider']} is reviewing your diff…[/dim]")
    with console.status("[dim] cross-examining…[/dim]", spinner="dots"):
        verdict = review_diff(config, diff, spec)
    render_verdict(console, verdict)
    if not verdict.passed:
        raise typer.Exit(1)


@app.command()
def kaizen(
    goal: Optional[str] = typer.Argument(
        None, help="What to improve. Omit to auto-pick the top FIXME/TODO in ronin's own source."),
    tests: Optional[str] = typer.Option(
        None, "--tests", help="Narrow the fitness gate to a path/expression (faster)."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Auto-apply if the fitness gate passes (no prompt)."),
    duel: Optional[str] = typer.Option(
        None, "--duel", help="Have a RIVAL provider[:model] red-team the proven diff before you approve it."),
) -> None:
    """改善 — ronin sharpens its own blade. Finds a weakness, drafts a fix in an
    isolated git worktree, and PROVES it against the test suite before showing
    you anything. The diff only reaches your working tree if the tests pass.

    Point it at a free provider (`/login cerebras`) and it improves code for $0.
    Something a single-vendor agent structurally won't let you do: an agent that
    edits its own source, with objective eval-proof it worked.
    """
    from .kaizen import run_kaizen

    config = load_config()
    duel_spec = None
    if duel:
        from .consensus import parse_model_spec
        duel_spec = parse_model_spec(duel)
    result = run_kaizen(config, Path("."), console, goal=goal, target_tests=tests,
                        auto_apply=yes, duel_against=duel_spec)
    style = "green" if result.applied else "yellow"
    console.print(f"[{style}]改 {result.note}[/{style}]")
    if result.fitness is not None and not result.applied and result.diff:
        console.print(f"[dim]  fitness: {result.fitness.summary}[/dim]")


# ---------- bench (model bake-off) ----------

@app.command()
def bench(
    models: str = typer.Option(
        ..., "--models", "-m",
        help="Comma-separated provider[:model] specs to benchmark, e.g. "
             "'anthropic,gemini,cerebras,ollama:llama3.1'.",
    ),
    threshold: float = typer.Option(
        0.8, "--threshold", help="Pass-rate bar (0..1) a model must clear to be recommended."),
) -> None:
    """Run the objective eval battery across several models and recommend the
    CHEAPEST one that clears the quality bar.

    Eval-driven model selection — pick the model with data, not vibes. Something
    a single-vendor agent has no way to do.
    """
    from .bench import parse_specs_or_exit, render_bench, run_bench

    config = load_config()
    specs = parse_specs_or_exit(models, console)
    labels = ", ".join(f"{s['provider']}{':' + s['model'] if s.get('model') else ''}" for s in specs)
    console.print(f"[dim]benchmarking {len(specs)} models on the objective battery — {labels}\n"
                  "several real model calls each, ~1–3 min on free tiers…[/dim]")
    with console.status("[dim] running evals…[/dim]", spinner="dots"):
        result = run_bench(config, specs, threshold=threshold)
    render_bench(console, result)


# ---------- tools ----------

@app.command()
def tools() -> None:
    """List the tools that are wired up for the current config."""
    config = load_config()
    services = config.configured_services()
    tool_list = build_tools(config)

    table = Table(title="Configured tools", box=box.ROUNDED, show_lines=False)
    table.add_column("Service", style="cyan")
    table.add_column("Tool", style="bold")
    table.add_column("Description", style="dim", overflow="fold")

    for tool in tool_list:
        service = tool.name.split("_", 1)[0]
        table.add_row(service, tool.name, tool.description)
    if not tool_list:
        table.add_row("[red]none[/red]", "", "Run [bold]ronin init[/bold] or [bold]ronin init --demo[/bold].")

    console.print(table)
    if config.demo_mode:
        console.print("[yellow]demo mode is on[/yellow] — calls go to in-process fixtures, not real APIs.")
    elif services:
        console.print(f"[dim]configured services:[/dim] {', '.join(services)}")


# ---------- doctor ----------

def _provider_live_check(config: RoninConfig) -> str:
    """Ping the provider's models endpoint to verify the key actually works and
    the configured model exists. Returns a Rich-markup status string."""
    import urllib.error
    import urllib.request

    provider = config.provider
    model = config.resolved_model()
    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        key = config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return "[red]no key set[/red]"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif provider == "ollama":
        base = config.resolved_base_url() or "http://localhost:11434/v1"
        url = f"{base.rstrip('/')}/models"
        headers = {}
        key = None
    else:
        base = config.resolved_base_url() or "https://api.openai.com/v1"
        url = f"{base.rstrip('/')}/models"
        key = config.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            return "[red]no key set[/red]"
        headers = {"Authorization": f"Bearer {key}"}

    # A non-Python User-Agent: Groq (and other WAF-fronted APIs) 403 the default
    # "Python-urllib" agent, which would make this check lie about a valid key.
    headers["User-Agent"] = "ronin (+https://github.com/rohithkandula19/Ronin)"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            import json as _json
            data = _json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return f"[red]invalid key ({e.code})[/red]"
        return f"[red]HTTP {e.code}[/red]"
    except Exception as e:  # noqa: BLE001
        return f"[red]unreachable[/red] [dim]({e.__class__.__name__})[/dim]"

    ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)]
    if ids and model not in ids:
        return f"[green]key ok[/green] · [red]model '{model}' not found[/red] [dim](try: {', '.join(ids[:3])}…)[/dim]"
    return "[green]ok — key + model valid[/green]"


@app.command()
def doctor(
    check: bool = typer.Option(False, "--check", help="Ping the provider to verify the key + model actually work (network)."),
) -> None:
    """Health check: config location, provider auth, configured services.

    Add --check to make a live call that confirms the key is valid and the
    configured model exists (instead of just checking a key is present)."""
    config = load_config()
    path = find_config_path()

    table = Table(box=box.ROUNDED, show_header=False)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()

    table.add_row("config file", str(path) if path else "[red]none[/red] — run ronin init")
    table.add_row("demo mode", "[green]yes[/green]" if config.demo_mode else "[dim]no[/dim]")
    table.add_row("provider", config.provider)
    table.add_row("model", config.resolved_model())
    if config.resolved_base_url():
        table.add_row("base_url", config.resolved_base_url() or "")
    # Static check only confirms a key is *present* — say so honestly.
    table.add_row(
        f"{config.provider} key",
        "[green]present[/green]" if config.has_provider_auth() else "[red]missing[/red]",
    )
    if check:
        table.add_row("live check", _provider_live_check(config))
    services = config.configured_services()
    table.add_row("services", ", ".join(services) if services else "[red]none[/red]")
    table.add_row("ronin version", __version__)

    console.print(table)
    if not check:
        console.print("[dim]tip: run [bold]ronin doctor --check[/bold] to verify the key + model actually work.[/dim]")


# ---------- saved queries ----------

@app.command()
def save(
    name: str = typer.Argument(..., help="Short slug-style name. Letters / digits / '-' / '_' only."),
    query: list[str] = typer.Argument(..., help="The question to save. Quote multi-word questions."),
    description: str = typer.Option("", "--description", "-d", help="Optional one-line description."),
) -> None:
    """Save a question under NAME for later recall via `ronin run NAME`."""
    path = queries_path()
    store = QueryStore.load(path)
    text = " ".join(query)
    try:
        store.add(name, text, description=description)
    except ValueError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(2)
    store.save(path)
    console.print(f"[green]✓[/green] saved [bold]{name}[/bold]: {text}")
    console.print(f"[dim]run it with:[/dim] [bold]ronin run {name}[/bold]")


@app.command()
def run(
    name: str = typer.Argument(..., help="Name of a saved query."),
    raw: bool = typer.Option(False, "--raw", help="Plain output."),
) -> None:
    """Run a saved query as if you typed `ronin ask "<saved text>"`."""
    store = QueryStore.load(queries_path())
    try:
        saved = store.get(name)
    except KeyError:
        console.print(f"[red]✗[/red] no saved query named [bold]{name}[/bold]. See [bold]ronin queries[/bold].")
        raise typer.Exit(2)

    config = load_config()
    if not config.has_provider_auth():
        console.print(
            f"[red]✗[/red] No credentials for provider [bold]{config.provider}[/bold]. Run [bold]ronin init[/bold] first."
        )
        raise typer.Exit(2)

    console.print(f"[dim]running saved query[/dim] [bold]{name}[/bold]: {saved.query}")
    result = run_ask(config, saved.query, console=console)
    _print_result(result, raw=raw)


@app.command()
def queries() -> None:
    """List all saved queries."""
    store = QueryStore.load(queries_path())
    items = store.list_all()
    if not items:
        console.print("[dim]no saved queries yet. Try:[/dim] [bold]ronin save mrr \"what is our MRR right now\"[/bold]")
        return
    table = Table(title="Saved queries", box=box.ROUNDED)
    table.add_column("name", style="cyan", no_wrap=True)
    table.add_column("query", overflow="fold")
    table.add_column("description", style="dim", overflow="fold")
    for q in items:
        table.add_row(q.name, q.query, q.description or "")
    console.print(table)


@app.command(name="unsave")
def unsave(name: str = typer.Argument(..., help="Name of the saved query to remove.")) -> None:
    """Remove a saved query."""
    path = queries_path()
    store = QueryStore.load(path)
    if not store.remove(name):
        console.print(f"[red]✗[/red] no saved query named [bold]{name}[/bold]")
        raise typer.Exit(2)
    store.save(path)
    console.print(f"[green]✓[/green] removed [bold]{name}[/bold]")


# ---------- eval (delegates to the eval-suite CLI) ----------

eval_app = typer.Typer(help="Measure agent quality (bare 'ronin eval'); golden-dataset judge runs and drift as subcommands.",
                       invoke_without_command=True)
app.add_typer(eval_app, name="eval")


@eval_app.callback(invoke_without_command=True)
def eval_default(
    ctx: typer.Context,
    model: str = typer.Option(None, "--model", help="Evaluate a specific model on the current provider."),
) -> None:
    """Bare ``ronin eval`` → score the agent on objective tasks (no LLM judge)."""
    if ctx.invoked_subcommand is not None:
        return  # a subcommand (run / drift) was given
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold]. "
                      "Run [bold]ronin login[/bold] or [bold]ronin init[/bold] first.")
        raise typer.Exit(2)
    from .agent_eval import render_report, run_eval
    shown = model or config.resolved_model()
    console.print(f"[dim]running agent eval on [bold]{config.provider} · {shown}[/bold] — "
                  "several real model calls, ~1–2 min on free tiers…[/dim]")
    with console.status("[dim] evaluating…[/dim]", spinner="dots"):
        outcomes = run_eval(config, model=model)
    render_report(console, config, outcomes, model=model)


@eval_app.command("run", help="Run a golden dataset against a target model.")
def eval_run(
    dataset: Path = typer.Argument(..., help="Path to JSONL dataset."),
    target: str = typer.Option("claude-sonnet-4-6", "--target"),
    judge: str = typer.Option("claude-opus-4-7", "--judge"),
    criteria: str = typer.Option(
        "task_success,faithfulness,helpfulness,safety", "--criteria",
        help="Comma-separated rubric criteria.",
    ),
    label: Optional[str] = typer.Option(None, "--label"),
    json_out: str = typer.Option("eval-report.json", "--json-out"),
    out: Optional[str] = typer.Option(None, "--out", help="Optional HTML report path."),
) -> None:
    from ronin_eval_suite.cli import main as eval_main

    argv = ["run", str(dataset), "--target", target, "--judge", judge, "--criteria", criteria, "--json-out", json_out]
    if label:
        argv += ["--label", label]
    if out:
        argv += ["--out", out]
    raise typer.Exit(eval_main(argv))


@eval_app.command("drift", help="Compare two run reports; exit non-zero on regression.")
def eval_drift(
    baseline: Path = typer.Argument(...),
    candidate: Path = typer.Argument(...),
    threshold: float = typer.Option(0.5, "--threshold"),
) -> None:
    from ronin_eval_suite.cli import main as eval_main

    argv = ["drift", str(baseline), str(candidate), "--threshold", str(threshold)]
    raise typer.Exit(eval_main(argv))


# ---------- mcp ----------

mcp_app = typer.Typer(help="Connect MCP servers (Anthropic's tool protocol) — their tools join the agent.")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("list", help="List configured MCP servers and their tools.")
def mcp_list() -> None:
    from .mcp_client import build_mcp_tools, load_mcp_servers
    servers = load_mcp_servers(".")
    if not servers:
        console.print("[dim]no MCP servers configured. Add one:[/dim]\n"
                      "  [cyan]ronin mcp add fs npx -y @modelcontextprotocol/server-filesystem .[/cyan]")
        return
    for name, spec in servers.items():
        console.print(f"[bold]{name}[/bold]  [dim]{spec.get('command', '')} "
                      f"{' '.join(spec.get('args', []))}[/dim]")
    console.print("\n[dim]connecting to verify…[/dim]")
    tools = build_mcp_tools(".", console=console)
    console.print(f"[green]✓[/green] {len(tools)} tool(s) available to the agent.")


@mcp_app.command("remove", help="Remove a configured MCP server by name.")
def mcp_remove(name: str = typer.Argument(..., help="The server name to remove.")) -> None:
    from .mcp_client import remove_mcp_server
    if remove_mcp_server(name, "."):
        console.print(f"[green]✓[/green] removed MCP server [bold]{name}[/bold].")
    else:
        console.print(f"[yellow]no MCP server named[/yellow] [bold]{name}[/bold] [yellow]configured.[/yellow]")


@mcp_app.command("add", help="Add an MCP server: ronin mcp add NAME COMMAND [ARGS...]",
                 context_settings={"ignore_unknown_options": True})
def mcp_add(
    name: str = typer.Argument(..., help="A short name, e.g. 'fs'."),
    command: str = typer.Argument(..., help="The server command, e.g. 'npx'."),
    args: Optional[list[str]] = typer.Argument(None, help="Args for the server command (flags like -y are passed through)."),
) -> None:
    from .mcp_client import add_mcp_server
    path = add_mcp_server(name, command, list(args or []), ".")
    console.print(f"[green]✓[/green] added MCP server [bold]{name}[/bold] → [cyan]{path}[/cyan]")
    console.print("[dim]its tools load automatically next time you run [bold]ronin[/bold]. "
                  "Verify now with [bold]ronin mcp list[/bold].[/dim]")


# ---------- plugins ----------

@app.command()
def plugins() -> None:
    """Discover and list user plugins from .ronin/plugins/."""
    from .plugins import find_plugin_dir, load_plugins

    plugin_dir = find_plugin_dir()
    results = load_plugins(plugin_dir)

    if not plugin_dir.exists():
        console.print(
            f"[dim]no plugin dir at[/dim] [cyan]{plugin_dir}[/cyan][dim] yet. "
            "Drop a .py file with a register_tools() function there to add custom tools.[/dim]"
        )
        return
    if not results:
        console.print(f"[dim]no plugins found in[/dim] [cyan]{plugin_dir}[/cyan]")
        return

    table = Table(title=f"Plugins from {plugin_dir}", box=box.ROUNDED)
    table.add_column("plugin", style="cyan", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("tools / error", overflow="fold")
    for r in results:
        if r.error:
            table.add_row(r.name, "[red]error[/red]", r.error)
        else:
            tool_names = ", ".join(t.name for t in r.tools) or "[dim](none)[/dim]"
            table.add_row(r.name, "[green]ok[/green]", tool_names)
    console.print(table)


# ---------- serve ----------

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Run ronin as an HTTP API. POST /ask, GET /health."""
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for provider [bold]{config.provider}[/bold]. Run [bold]ronin init[/bold] first.")
        raise typer.Exit(2)

    try:
        import uvicorn
    except ImportError:
        console.print("[red]✗[/red] uvicorn not installed. Run: [bold]uv pip install uvicorn[/bold]")
        raise typer.Exit(2)

    from .server import make_app

    app_obj = make_app(config)
    console.print(Panel.fit(
        f"[bold cyan]ronin serve[/bold cyan]\n"
        f"http://{host}:{port}/health   →   health check\n"
        f"POST http://{host}:{port}/ask  →   {{\"question\": \"...\"}}\n\n"
        f"provider: {config.provider} · model: {config.resolved_model()} · "
        f"services: {', '.join(config.configured_services()) or 'none'}",
        title="ready", border_style="green",
    ))
    uvicorn.run(app_obj, host=host, port=port, log_level="info")


# ---------- export (session → markdown) ----------

@app.command()
def export(
    session_id: str = typer.Argument(None, help="Session id to export. Omit for the latest."),
    out: str = typer.Option(None, "--out", help="Write to this file (default: print to stdout)."),
    root: Path = typer.Option(Path("."), "--root", help="Repo whose session to export."),
) -> None:
    """📤 Export a session as shareable markdown (to a file with --out, else stdout)."""
    from .replay import session_to_markdown, transcript_to_turns
    from .sessions import latest_session, list_sessions, load_session

    sid = session_id or latest_session(root)
    if not sid:
        console.print("[dim]no sessions to export.[/dim]")
        raise typer.Exit(1)
    turns = transcript_to_turns(load_session(sid))
    title = next((s.get("title") for s in list_sessions() if str(s.get("id")) == str(sid)), "ronin session")
    md = session_to_markdown(turns, title=title or "ronin session")
    if out:
        try:
            Path(out).write_text(md, encoding="utf-8")
            console.print(f"[green]✓ exported →[/green] [cyan]{out}[/cyan] ({len(turns)} entries)")
        except OSError as e:
            console.print(f"[red]couldn't write {out}: {e}[/red]")
            raise typer.Exit(1)
    else:
        import sys
        sys.stdout.write(md)


# ---------- recall (search past sessions) ----------

@app.command()
def recall(
    query: str = typer.Argument(..., help="What to search your past sessions for."),
    here: bool = typer.Option(False, "--here", help="Only this repo's sessions (default: all)."),
    limit: int = typer.Option(8, "--limit", help="Max results."),
    root: Path = typer.Option(Path("."), "--root", help="Repo (with --here)."),
) -> None:
    """🔎 Search your past sessions and show the best-matching turn from each.

    `ronin recall "auth bug"` searches every stored session (use --here to scope
    to this repo), then `ronin replay <id>` plays a hit back in full.
    """
    from .recall import rank
    from .sessions import list_sessions, load_session

    metas = list_sessions(root if here else None)
    items = [(m, load_session(str(m.get("id")))) for m in metas]
    hits = rank(items, query, limit=limit)
    if not hits:
        scope = "this repo's" if here else "any"
        console.print(f"[dim]no {scope} sessions matched [bold]{query}[/bold].[/dim]")
        return
    console.print(f"[#6b7089]{len(hits)} match(es) for [bold]{query}[/bold]:[/#6b7089]")
    for h in hits:
        console.print(f"\n[cyan]{h.session_id[:14]}[/cyan] [dim]· score {h.score} ·[/dim] "
                      f"[bold]{h.title[:60]}[/bold]")
        if h.snippet:
            console.print(f"  [#c0caf5]{h.snippet}[/#c0caf5]")
    console.print(f"\n[dim]replay one with [bold]ronin replay <id>[/bold].[/dim]")


# ---------- replay (session history) ----------

@app.command()
def replay(
    session_id: str = typer.Argument(None, help="Session id to replay. Omit for the latest."),
    list_sessions_flag: bool = typer.Option(False, "--list", help="List this repo's sessions and exit."),
    root: Path = typer.Option(Path("."), "--root", help="Repo whose sessions to read."),
) -> None:
    """⏪ Replay a past session as a readable story (who said what, trimmed).

    `ronin replay` plays the latest; `ronin replay --list` shows all sessions to
    pick an id from.
    """
    from .replay import render_story, transcript_to_turns
    from .sessions import latest_session, list_sessions, load_session

    if list_sessions_flag:
        sessions = list_sessions(root)
        if not sessions:
            console.print("[dim]no saved sessions for this repo yet.[/dim]")
            return
        table = Table(title="sessions", box=box.ROUNDED)
        table.add_column("id", style="cyan", no_wrap=True)
        table.add_column("turns", justify="right")
        table.add_column("title")
        for s in sessions[:40]:
            table.add_row(str(s.get("id", "?"))[:12], str(s.get("turns", 0)), s.get("title", "")[:70])
        console.print(table)
        return

    sid = session_id or latest_session(root)
    if not sid:
        console.print("[dim]no sessions to replay. Run [bold]ronin code[/bold] first.[/dim]")
        raise typer.Exit(1)
    turns = transcript_to_turns(load_session(sid))
    console.print(f"[#6b7089]replaying session [bold]{str(sid)[:12]}[/bold] · {len(turns)} entries[/#6b7089]")
    render_story(console, turns)


# ---------- map (codebase onboarding) ----------

@app.command(name="map")
def map_cmd(
    write: bool = typer.Option(False, "--write", help="Save the overview as RONIN.md (project memory)."),
    root: Path = typer.Option(Path("."), "--root", help="Repo to map."),
) -> None:
    """🗺 Onboard to a codebase fast — ronin reads the repo and produces a concise
    architecture overview (what it is, key dirs, entry points, how to build/test).
    With --write it saves the result as RONIN.md so every future run is grounded.
    """
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .code_mode import run_code_agent
    console.print("[#6b7089]mapping the codebase…[/#6b7089]")
    prompt = (
        "Produce a concise architecture overview of THIS repository for a new "
        "contributor. Use your read-only tools (list_files / glob / search / read) "
        "to ground every claim — do not guess. Cover, as markdown:\n"
        "## What this project is (1–2 sentences)\n## Key directories\n"
        "## Entry points\n## Build / test / run commands\n## Conventions worth knowing\n"
        "Keep it tight and high-signal."
    )
    result = run_code_agent(config, prompt, root=root, console=console, read_only=True,
                            include_image_tool=False, max_iterations=12)
    if write and result.output:
        path = Path(root) / "RONIN.md"
        header = "# RONIN.md\n\n*Architecture overview generated by `ronin map`. Edit freely.*\n\n"
        try:
            path.write_text(header + result.output.strip() + "\n", encoding="utf-8")
            console.print(f"[green]✓ saved project memory →[/green] [cyan]{path}[/cyan]")
        except OSError as e:
            console.print(f"[yellow]couldn't write RONIN.md: {e}[/yellow]")


# ---------- setup (first-run wizard) ----------

@app.command()
def setup() -> None:
    """🧰 Get routing set up in one go — suggests a free/cheap blade for simple
    turns and a strong blade for hard ones (the Cost Router + Scout→Strike), based
    on the providers you already have keys for, and saves it.
    """
    config = load_config()
    console.print(f"[#6b7089]current provider:[/#6b7089] [bold]{config.provider}[/bold] · "
                  f"[#6b7089]model:[/#6b7089] {config.resolved_model()}")
    if config.route_fast and config.route_strong:
        console.print(f"[green]routing already on[/green] — simple→[bold]{config.route_fast}[/bold] · "
                      f"complex→[bold]{config.route_strong}[/bold]")
        if not Confirm.ask("Re-configure it?", default=False):
            return

    from .setup_wizard import suggest_routing
    suggestion = suggest_routing(config)
    if suggestion is None:
        console.print("[yellow]Couldn't suggest a free+strong split[/yellow] — you only have one "
                      "provider configured. Add a free one (e.g. [bold]ronin login cerebras[/bold]) "
                      "then re-run [bold]ronin setup[/bold].")
        return
    fast, strong = suggestion
    console.print("\n[bold]Suggested routing[/bold] (Cost Router):")
    console.print(f"  simple turns  → [cyan]{fast}[/cyan]  [dim](free/cheap)[/dim]")
    console.print(f"  complex turns → [cyan]{strong}[/cyan]  [dim](strong)[/dim]")
    if not Confirm.ask("\nEnable this routing?", default=True):
        console.print("[dim]left routing off.[/dim]")
        return
    from .config import save_config
    config = config.model_copy(update={"route_fast": fast, "route_strong": strong})
    path = save_config(config, scope="project")
    console.print(f"[green]✓ routing enabled[/green] → saved to [cyan]{path}[/cyan]\n"
                  "[dim]every session now shows 💰 cost · saved $… and self-tunes over time.[/dim]")


# ---------- leaderboard (dojo standings) ----------

@app.command()
def leaderboard(
    reset: bool = typer.Option(False, "--reset", help="Clear the dojo leaderboard."),
    root: Path = typer.Option(Path("."), "--root", help="Repo whose dojo history to show."),
) -> None:
    """🏆 Standings from every `ronin dojo` you've run here — which provider keeps
    winning your tasks, and the route_strong it recommends.
    """
    from .leaderboard import leaderboard_path, load_leaderboard

    if reset:
        try:
            leaderboard_path(root).unlink()
            console.print("[green]✓ leaderboard cleared[/green]")
        except OSError:
            console.print("[dim]nothing to reset.[/dim]")
        return

    lb = load_leaderboard(root)
    standings = lb.standings()
    if not standings:
        console.print("[dim]no dojos recorded yet. Run [bold]ronin dojo \"<task>\" -m a,b,c[/bold].[/dim]")
        return
    table = Table(title="🏆 dojo leaderboard", box=box.ROUNDED)
    table.add_column("#", justify="right", style="#6b7089")
    table.add_column("provider", style="bold")
    table.add_column("wins", justify="right")
    table.add_column("contests", justify="right")
    table.add_column("win rate", justify="right", style="cyan")
    for i, r in enumerate(standings, 1):
        table.add_row(str(i), r["provider"], str(r["wins"]), str(r["contests"]),
                      f"{r['rate']*100:.0f}%")
    console.print(table)
    rec = lb.recommend_strong()
    if rec:
        console.print(f"[green]→ recommended[/green] [bold]route_strong = {rec}[/bold] "
                      "[dim](it keeps winning your dojos)[/dim]")
    else:
        console.print("[dim]not enough contests for a confident recommendation yet.[/dim]")


# ---------- router (self-tuning insight) ----------

@app.command()
def router(
    reset: bool = typer.Option(False, "--reset", help="Forget everything the router has learned."),
    root: Path = typer.Option(Path("."), "--root", help="Repo whose routing stats to show."),
) -> None:
    """🧭 Show what the Self-tuning Router has learned about each blade in this
    repo — per (tier, provider) success rate and whether it's being escalated.
    """
    from .router_stats import rows, stats_path

    path = stats_path(root)
    if reset:
        try:
            path.unlink()
            console.print("[green]✓ router memory reset[/green]")
        except OSError:
            console.print("[dim]nothing to reset.[/dim]")
        return

    from .router_stats import load_stats
    data = rows(load_stats(root))
    if not data:
        console.print(f"[dim]the router hasn't learned anything yet at[/dim] [cyan]{path}[/cyan]"
                      "[dim]. Set route_fast/route_strong and run a few turns.[/dim]")
        return
    _status_style = {"reliable": "[green]reliable[/green]", "escalate": "[red]escalate[/red]",
                     "learning": "[#6b7089]learning[/#6b7089]"}
    table = Table(title="learned routing", box=box.ROUNDED)
    table.add_column("tier", style="cyan")
    table.add_column("provider", style="bold")
    table.add_column("success", justify="right")
    table.add_column("rate", justify="right")
    table.add_column("status")
    for r in data:
        rate = f"{r['rate']*100:.0f}%" if r["rate"] is not None else "—"
        table.add_row(r["tier"], r["provider"], f"{r['ok']}/{r['total']}", rate,
                      _status_style.get(r["status"], r["status"]))
    console.print(table)
    console.print("[dim]escalate = proven unreliable here → routed to the strong blade.[/dim]")


# ---------- costs ----------

@app.command()
def costs(
    by: str = typer.Option("model", "--by", help="Group by 'model' or 'day'."),
) -> None:
    """Show token + cost usage recorded by previous ronin commands, plus lifetime
    Cost-Router savings (when routing is in use)."""
    from .usage import load_records, summarize, usage_path

    # Lifetime Cost-Router savings (from .ronin/savings.jsonl) — shown first so it
    # appears even before any per-call usage records exist.
    from .savings import load_summary
    _sv = load_summary(Path("."))
    if _sv["turns"]:
        panel = Table(box=box.ROUNDED, show_header=False, title="🪙 Cost Router (lifetime)")
        panel.add_column(style="cyan", no_wrap=True)
        panel.add_column(style="bold")
        panel.add_row("routed turns", str(_sv["turns"]))
        panel.add_row("free turns", f"{_sv['free_turns']}/{_sv['turns']}")
        panel.add_row("spent", f"${_sv['spent']:.4f}")
        panel.add_row("saved vs all-strong", f"[green]${_sv['saved']:.4f}[/green]")
        console.print(panel)

    records = load_records()
    if not records:
        console.print(
            f"[dim]no usage recorded yet at[/dim] [cyan]{usage_path()}[/cyan][dim]. "
            "Run [bold]ronin ask[/bold] a few times to populate.[/dim]"
        )
        return

    summary = summarize(records)
    header = Table(box=box.ROUNDED, show_header=False)
    header.add_column(style="cyan", no_wrap=True)
    header.add_column(style="bold")
    header.add_row("total calls", str(summary.total_calls))
    header.add_row("input tokens", f"{summary.total_input_tokens:,}")
    header.add_row("output tokens", f"{summary.total_output_tokens:,}")
    header.add_row("total cost", f"${summary.total_cost_usd:.4f}")
    console.print(header)

    bucket = summary.by_model if by == "model" else summary.by_day
    label = "model" if by == "model" else "day"
    table = Table(title=f"By {label}", box=box.ROUNDED)
    table.add_column(label, style="cyan")
    table.add_column("calls", justify="right")
    table.add_column("input", justify="right")
    table.add_column("output", justify="right")
    table.add_column("cost", justify="right", style="bold")
    for key in sorted(bucket.keys()):
        s = bucket[key]
        table.add_row(
            key, str(s.total_calls),
            f"{s.total_input_tokens:,}", f"{s.total_output_tokens:,}",
            f"${s.total_cost_usd:.4f}",
        )
    console.print(table)


# ---------- tui ----------

@app.command()
def tui() -> None:
    """Launch a full-screen TUI (Textual) — chat pane + live trace + input box."""
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for provider [bold]{config.provider}[/bold]. Run [bold]ronin init[/bold] first.")
        raise typer.Exit(2)
    try:
        from .tui import run_tui
    except ImportError as exc:
        console.print(f"[red]✗[/red] textual not installed: {exc}. Try [bold]uv pip install textual[/bold].")
        raise typer.Exit(2)
    run_tui(config=config)


# ---------- briefing ----------

@app.command()
def briefing(
    out: Optional[Path] = typer.Option(None, "--out", help="Write briefing to a Markdown file."),
    raw: bool = typer.Option(False, "--raw", help="Print plain Markdown (no Rich panel)."),
    slack: Optional[str] = typer.Option(None, "--slack", help="Post the briefing to a Slack channel (#founders or C123…)."),
    email: Optional[str] = typer.Option(None, "--email", help="Send the briefing to an email address (requires RESEND_API_KEY)."),
    template: Optional[Path] = typer.Option(None, "--template", help="Path to a briefing-template.toml. Defaults to .ronin/briefing-template.toml if present."),
    history: bool = typer.Option(False, "--history", help="Show a trend table of past briefings instead of running a new one."),
    no_save: bool = typer.Option(False, "--no-save", help="Don't persist this run to .ronin/briefings/."),
) -> None:
    """Weekly founder briefing: revenue, churn, payment failures, top engineering issues.

    Works offline in demo mode; uses Claude (or your configured provider) in real mode.
    Output is Markdown — paste into Slack, email, or a doc.

    Each run is auto-saved to ``.ronin/briefings/<date>.json`` so subsequent runs can
    show week-over-week deltas inline. Use ``--history`` to see the trend table.
    Use ``--slack <channel>`` to post the briefing directly.
    """
    from .briefing import compute_briefing_data, render_briefing_md
    from .briefing_history import (
        BriefingSnapshot,
        format_delta_line,
        load_snapshots,
        most_recent_prior,
        save_snapshot,
    )
    from .briefing_history import BriefingDelta
    from .tools import build_tools

    config = load_config()

    # --history mode: just show the trend table and exit.
    if history:
        snapshots = load_snapshots()
        if not snapshots:
            console.print(
                "[dim]no briefing history yet at[/dim] [cyan].ronin/briefings/[/cyan][dim]. "
                "Run [bold]ronin briefing[/bold] once to start the trend.[/dim]"
            )
            return
        table = Table(title="Briefing history", box=box.ROUNDED)
        table.add_column("date", style="cyan", no_wrap=True)
        table.add_column("MRR", justify="right")
        table.add_column("active", justify="right")
        table.add_column("new/wk", justify="right")
        table.add_column("churn/wk", justify="right")
        table.add_column("failed/wk", justify="right", style="red")
        table.add_column("urgent open", justify="right", style="yellow")
        for s in snapshots:
            table.add_row(
                s.date,
                f"${s.mrr_cents / 100:,.0f}",
                str(s.active_subs),
                str(s.new_subs_7d),
                str(s.churned_subs_7d),
                str(s.failed_charges_7d),
                str(s.urgent_open_issues),
            )
        console.print(table)
        return

    tools = build_tools(config)
    data = compute_briefing_data(tools)

    # If a template TOML is configured (--template flag or .ronin/briefing-template.toml),
    # use it. Otherwise render the four default sections.
    from .briefing_template import BriefingTemplate, render_with_template
    template_obj = BriefingTemplate.load(template)
    if template is not None or (template_obj.sections != ["revenue", "payments", "engineering", "actions"] or template_obj.title != "Founder briefing — {{date}}"):
        md = render_with_template(data, template_obj)
    else:
        md = render_briefing_md(data)

    # Append "vs last week" line if we have a prior snapshot.
    snapshot = BriefingSnapshot.from_briefing(data)
    prior = most_recent_prior(snapshot.date)
    if prior is not None:
        delta = BriefingDelta.compute(snapshot, prior)
        md = md.rstrip() + "\n\n" + format_delta_line(delta, prior.date) + "\n"

    # Statistical anomalies vs trailing weeks (only fires after ≥4 prior runs).
    from .briefing_anomaly import detect_anomalies, render_anomalies_section
    history = load_snapshots()
    anomalies = detect_anomalies(history, snapshot)
    if anomalies:
        md = md.rstrip() + "\n\n" + render_anomalies_section(anomalies) + "\n"

    # Persist for trending (unless --no-save).
    if not no_save:
        save_snapshot(snapshot)

    if out:
        out.write_text(md, encoding="utf-8")
        console.print(f"[green]✓[/green] wrote briefing to [cyan]{out}[/cyan]")

    if slack:
        bot_token = config.slack_bot_token or os.environ.get("SLACK_BOT_TOKEN")
        if not bot_token:
            console.print(
                "[red]✗[/red] No SLACK_BOT_TOKEN configured. Run [bold]ronin init[/bold] or "
                "set the env var (needs the chat:write scope)."
            )
            raise typer.Exit(2)
        from .briefing_slack import post_briefing_to_slack
        try:
            resp = post_briefing_to_slack(bot_token, slack, md)
            console.print(f"[green]✓[/green] posted to [cyan]{slack}[/cyan] (ts={resp.get('ts')})")
        except (RuntimeError, ValueError) as exc:
            console.print(f"[red]✗[/red] {exc}")
            raise typer.Exit(2)

    if email:
        if not os.environ.get("RESEND_API_KEY"):
            console.print(
                "[red]✗[/red] No RESEND_API_KEY set. Get one at "
                "[underline]https://resend.com/api-keys[/underline] then "
                "[bold]export RESEND_API_KEY=re_...[/bold]"
            )
            raise typer.Exit(2)
        from .briefing_email import send_briefing_email
        try:
            resp = send_briefing_email(email, md)
            console.print(f"[green]✓[/green] emailed to [cyan]{email}[/cyan] (id={resp.get('id', '?')})")
        except (ValueError, RuntimeError) as exc:
            console.print(f"[red]✗[/red] {exc}")
            raise typer.Exit(2)

    if raw:
        sys.stdout.write(md)
        return
    from rich.markdown import Markdown
    console.print(Markdown(md))


# ---------- agent ----------

@app.command()
def agent(
    goal: list[str] = typer.Argument(..., help="The goal for the agent to pursue. Quote multi-word goals."),
    confirm: bool = typer.Option(False, "--confirm", help="Pause for y/N approval before each tool call (Cline-style)."),
    max_steps: int = typer.Option(15, "--max-steps", help="Iteration cap for the autonomous loop."),
    raw: bool = typer.Option(False, "--raw", help="Plain output."),
) -> None:
    """Autonomous multi-step agent: give it a goal, it chains tool calls until done.

    Unlike `ronin ask` (one-shot) and `ronin chat` (turn-by-turn), `agent` runs an
    autonomous loop, narrating each step live. With --confirm it pauses for your
    approval before every tool call.
    """
    config = load_config()
    from .agent_mode import has_real_key, run_agent

    if not has_real_key(config):
        console.print(
            f"[red]✗[/red] Agent mode needs a real LLM — the offline demo brain can't reason multi-step. "
            f"Set credentials for provider [bold]{config.provider}[/bold] (e.g. [bold]export ANTHROPIC_API_KEY=...[/bold])."
        )
        raise typer.Exit(2)

    text = " ".join(goal)
    console.print(Panel.fit(f"[bold]Goal:[/bold] {text}", border_style="cyan", title="ronin agent"))

    result = run_agent(config, text, console=console, confirm=confirm, max_iterations=max_steps)

    console.print()
    if result.blocked:
        console.print(f"[red]✗[/red] {result.output}")
        raise typer.Exit(2)
    # If the answer already streamed inline, don't re-box it.
    if result.streamed:
        console.print("[bold green]✅ done[/bold green]")
    else:
        console.print(Panel(result.output or "[dim](no answer)[/dim]", title="Answer", border_style="green", padding=(1, 2)))
    meta = f"iterations: {result.iterations}"
    if result.usage:
        meta += f" · in: {result.usage.get('input_tokens', 0)} · out: {result.usage.get('output_tokens', 0)}"
    console.print(f"[dim]{meta}[/dim]")
    if raw:
        sys.stdout.write(result.output + "\n")


# ---------- code ----------

@app.command()
def code(
    task: list[str] = typer.Argument(None, help="The coding task. Omit to start an interactive session."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory the agent works in."),
    yolo: bool = typer.Option(False, "--yolo", help="Auto-approve writes + commands (trusted sandboxes only)."),
    max_steps: int = typer.Option(25, "--max-steps", help="Iteration cap."),
    init_memory: bool = typer.Option(False, "--init", help="Scaffold a RONIN.md project-memory file and exit."),
    continue_session: bool = typer.Option(False, "--continue", "-c", help="Resume this repo's last session."),
    plan: bool = typer.Option(False, "--plan", help="Propose a plan first (read-only), confirm, then execute."),
    scout: bool = typer.Option(False, "--scout", help="Scout→Strike: a free model does recon, the strong one edits (needs routing)."),
) -> None:
    """Coding agent (Claude Code / Cline shaped): reads files, edits code, runs commands.

    Give a task for a one-shot run, or omit it to drop into an interactive
    session (steer across turns, :undo to revert, :q to quit). Every write and
    shell command is gated behind your y/N approval by default with a diff
    preview; read operations run freely. --yolo auto-approves everything.

    Reference files with @path. --plan proposes steps before editing.
    --continue resumes your last session. Auto-loads RONIN.md / CLAUDE.md /
    AGENTS.md; --init scaffolds a RONIN.md.
    """
    if init_memory:
        from .project_memory import write_memory_template
        path = write_memory_template(root)
        console.print(f"[green]✓[/green] project memory at [cyan]{path}[/cyan] — edit it, then run [bold]ronin code[/bold].")
        return

    config = load_config()
    from .agent_mode import has_real_key
    from .code_mode import run_code_agent, run_code_session

    if not has_real_key(config):
        console.print(
            f"[red]✗[/red] Code mode needs a real LLM. Set credentials for provider "
            f"[bold]{config.provider}[/bold] (e.g. [bold]export ANTHROPIC_API_KEY=...[/bold])."
        )
        raise typer.Exit(2)

    # No task → interactive session (the Claude Code experience).
    if not task:
        run_code_session(config, root=root, console=console, yolo=yolo,
                         max_iterations=max_steps, continue_session=continue_session)
        return

    text = " ".join(task)
    console.print(Panel.fit(
        f"[bold]Task:[/bold] {text}\n[dim]root: {root.resolve()} · "
        f"{'YOLO (auto-approve)' if yolo else 'writes + commands need approval'}[/dim]",
        border_style="cyan", title="ronin code",
    ))

    # Plan mode: propose steps (read-only) → confirm → execute.
    if plan:
        console.print("[bold #2dd4bf]📋 planning (read-only)…[/bold #2dd4bf]")
        plan_res = run_code_agent(
            config, f"Produce a concise step-by-step PLAN to accomplish: {text}. "
            "Explore with read-only tools if needed, but DO NOT edit anything — just list the steps.",
            root=root, console=console, yolo=yolo, max_iterations=max_steps, read_only=True,
        )
        console.print()
        if not Confirm.ask("[bold]Proceed with this plan?[/bold]", default=True):
            console.print("[dim]aborted — nothing changed[/dim]")
            return
        text = f"Follow this plan:\n{plan_res.output}\n\nNow implement it: {text}"
        console.print()

    if scout and not plan:
        from .scout import run_scout_strike
        result = run_scout_strike(config, text, root=root, console=console,
                                  max_steps=max_steps, yolo=yolo)
    else:
        result = run_code_agent(config, text, root=root, console=console, yolo=yolo, max_iterations=max_steps)

    console.print()
    if result.blocked:
        console.print(f"[red]✗[/red] {result.output}")
        raise typer.Exit(2)
    # If the summary already streamed inline, don't re-box it — just confirm.
    if result.streamed:
        console.print("[bold green]✅ done[/bold green]")
    else:
        console.print(Panel(result.output or "[dim](no summary)[/dim]", title="Done", border_style="green", padding=(1, 2)))
    meta = f"iterations: {result.iterations}"
    if result.usage:
        meta += f" · in: {result.usage.get('input_tokens', 0)} · out: {result.usage.get('output_tokens', 0)}"
    console.print(f"[dim]{meta}[/dim]")


# ---------- investigate ----------

@app.command()
def investigate(
    symptom: list[str] = typer.Argument(..., help="The business symptom to root-cause. Quote it."),
    root: Path = typer.Option(Path("."), "--root", help="Code repo to inspect for causes."),
    yolo: bool = typer.Option(False, "--yolo", help="Auto-approve git commands."),
    max_steps: int = typer.Option(20, "--max-steps", help="Iteration cap."),
) -> None:
    """Bridge business + code: root-cause a symptom across your data AND your codebase.

    The thing Claude Code can't do (no business data) and a BI tool can't do
    (can't read code). Give it a symptom — "failed payments spiked", "churn
    jumped" — and it pulls the ops data to quantify/timestamp it, then searches
    the code + git history to find the likely cause, and connects them.

    Read-only: reads data, reads code, runs read-only git commands (gated).
    """
    config = load_config()
    from .agent_mode import has_real_key
    from .investigate_mode import run_investigate

    if not has_real_key(config):
        console.print(
            f"[red]✗[/red] Investigate mode needs a real LLM. Set credentials for provider "
            f"[bold]{config.provider}[/bold] (e.g. [bold]export ANTHROPIC_API_KEY=...[/bold])."
        )
        raise typer.Exit(2)

    text = " ".join(symptom)
    console.print(Panel.fit(
        f"[bold]Symptom:[/bold] {text}\n[dim]data: {', '.join(config.configured_services()) or 'none'} · "
        f"code: {root.resolve()}[/dim]",
        border_style="cyan", title="ronin investigate",
    ))

    result = run_investigate(config, text, root=root, console=console, yolo=yolo, max_iterations=max_steps)

    console.print()
    if result.blocked:
        console.print(f"[red]✗[/red] {result.output}")
        raise typer.Exit(2)
    console.print(Panel(result.output or "[dim](inconclusive)[/dim]", title="Root-cause analysis", border_style="green", padding=(1, 2)))
    meta = f"iterations: {result.iterations}"
    if result.usage:
        meta += f" · in: {result.usage.get('input_tokens', 0)} · out: {result.usage.get('output_tokens', 0)}"
    console.print(f"[dim]{meta}[/dim]")


# ---------- fix ----------

@app.command()
def tdd(
    spec: str = typer.Argument(..., help="What to build, e.g. \"parse_duration handles '2h30m'\"."),
    test: str = typer.Option("pytest -q", "--test", help="Command that runs the test(s)."),
    rounds: int = typer.Option(6, "--rounds", help="Max implement→re-run rounds."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    yolo: bool = typer.Option(False, "--yolo", help="Auto-approve edits (sandbox use)."),
) -> None:
    """🔴🟢 Test-first: ronin writes a FAILING test for the spec, then implements
    until it (and the suite) pass — red → green, the disciplined way.

    Example:  ronin tdd "slugify lowercases and strips punctuation"
    """
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .code_mode import run_code_agent
    from .fix_mode import run_fix

    console.print("[bold #f7768e]🔴 writing a failing test first…[/bold #f7768e]")
    run_code_agent(
        config,
        f"Write a FAILING test that captures this requirement: {spec}\n\n"
        "Create or extend the appropriate test file using the project's existing "
        "test conventions. Do NOT implement the feature yet — write only the test, "
        "so it fails for the right reason.",
        root=root, console=console, yolo=yolo, max_iterations=12,
    )
    console.print("[bold #9ece6a]🟢 now implementing until it passes…[/bold #9ece6a]")
    ok = run_fix(config, test, root=root, console=console, max_rounds=rounds, yolo=yolo)
    console.print("[bold green]✅ green[/bold green]" if ok
                  else "[yellow]still red after the round limit — pick it up from here[/yellow]")
    raise typer.Exit(0 if ok else 1)


@app.command(name="fix")
def fix_cmd(
    command: list[str] = typer.Argument(..., help="The command to make pass, e.g. \"pytest -q\"."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    rounds: int = typer.Option(5, "--rounds", help="Max fix→re-run rounds."),
    yolo: bool = typer.Option(False, "--yolo", help="Auto-approve edits (sandbox use)."),
) -> None:
    """Autonomous fix-until-green: run a command, and if it fails, edit + re-run until it passes.

    Examples:  ronin fix "pytest -q"  ·  ronin fix "npm test"  ·  ronin fix "python app.py"
    """
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold]. "
                      "Run [bold]ronin login[/bold] or [bold]ronin init[/bold] first.")
        raise typer.Exit(2)
    from .fix_mode import run_fix
    ok = run_fix(config, " ".join(command), root=root, console=console, max_rounds=rounds, yolo=yolo)
    raise typer.Exit(0 if ok else 1)


# ---------- review ----------

@app.command()
def review(
    base: str = typer.Option(None, "--base", help="Review this branch vs a base ref (e.g. main)."),
    staged: bool = typer.Option(False, "--staged", help="Review only staged changes."),
    pr: int = typer.Option(None, "--pr", help="Review a GitHub PR by number (diff fetched via gh)."),
    comment: bool = typer.Option(False, "--comment", help="Post the review back onto the PR (needs --pr)."),
    root: Path = typer.Option(Path("."), "--root", help="Repo to review."),
) -> None:
    """AI code review of your changes — structured, severity-tagged findings (read-only).

    Reviews your working-tree diff by default; ``--staged`` for staged changes,
    ``--base main`` for the branch vs a base, or ``--pr N`` for a GitHub PR
    (add ``--comment`` to post the review onto it).
    """
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold]. "
                      "Run [bold]ronin login[/bold] or [bold]ronin init[/bold] first.")
        raise typer.Exit(2)
    from .review_mode import run_review
    run_review(config, root=root, base=base, staged=staged, pr=pr, comment=comment, console=console)


@app.command()
def triage(
    limit: int = typer.Option(10, "--limit", help="How many open issues to triage."),
    root: Path = typer.Option(Path("."), "--root", help="Repo to triage."),
) -> None:
    """🗂 Read open GitHub issues and draft a triage — suggested labels, priority,
    and a first-response sketch for each (read-only; nothing is posted).
    """
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .gh_helper import gh_available, open_issues
    if not gh_available():
        console.print("[yellow]the GitHub CLI 'gh' isn't installed.[/yellow] "
                      "[dim]brew install gh && gh auth login[/dim]")
        raise typer.Exit(2)
    issues = open_issues(root, limit=limit)
    if not issues:
        console.print("[dim]no open issues (or gh not authenticated).[/dim]")
        return
    console.print(f"[#6b7089]triaging {len(issues)} open issue(s)…[/#6b7089]")
    from .code_mode import run_code_agent
    listing = "\n\n".join(f"#{i['number']} {i['title']}\n{i['body'][:600]}" for i in issues)
    prompt = ("Triage these open GitHub issues. For each, suggest: labels (bug/feature/"
              "question/etc.), a priority (P0–P3), and a one-line first response. Be concise.\n\n"
              f"{listing}")
    run_code_agent(config, prompt, root=root, console=console, read_only=True,
                   include_image_tool=False, max_iterations=6)


# ---------- version ----------

@app.command()
def version() -> None:
    """Print the ronin version."""
    console.print(f"ronin {__version__}")


@app.command()
def memory(
    add: str = typer.Option(None, "--add", help="Add a fact to long-term memory."),
    clear: bool = typer.Option(False, "--clear", help="Forget everything."),
) -> None:
    """Show (or edit) what ronin remembers about you across sessions.

    Memory is persistent and user-global (~/.ronin/memory.json): ronin recalls
    these facts in every future session. The agent also saves facts itself via
    its `remember` tool when you share durable info.
    """
    from .memory_store import add_memory, forget_all, load_memories

    if clear:
        n = forget_all()
        console.print(f"[green]✓[/green] forgot {n} memory item(s).")
        return
    if add:
        ok = add_memory(add)
        console.print(f"[green]✓[/green] remembered: {add}" if ok else "[dim]already in memory[/dim]")
        return

    mems = load_memories()
    if not mems:
        console.print("[dim]no memories yet. ronin will remember durable facts as you chat, "
                      "or add one: [bold]ronin memory --add \"I prefer Python\"[/bold][/dim]")
        return
    from rich.panel import Panel
    body = "\n".join(f"[#2dd4bf]•[/#2dd4bf] {m['text']}" for m in mems)
    console.print(Panel(body, title=f"🧠 {len(mems)} thing(s) ronin remembers about you",
                        border_style="#2dd4bf", padding=(1, 2)))
    console.print("[dim]clear with [bold]ronin memory --clear[/bold][/dim]")


@app.command()
def panda(
    activity: str = typer.Argument(None, help="dancing | running | playing | sleeping (default: all)"),
    loops: int = typer.Option(3, "--loops", "-n", help="How many times to repeat each activity."),
) -> None:
    """Watch the ronin panda do its thing. No args = it cycles through them all."""
    activities = list(_PANDA_ACTIVITIES)
    if activity:
        if activity not in _PANDA_ACTIVITIES:
            console.print(f"[red]unknown activity[/red] '{activity}'. choose: {', '.join(activities)}")
            raise typer.Exit(2)
        activities = [activity]
    for name in activities:
        _render_panda(console, activity=name, loops=loops, tagline=False)


@app.command()
def image(
    prompt: list[str] = typer.Argument(..., help="What to draw, e.g. \"a red panda coding at night, neon\"."),
    out: Path = typer.Option(None, "--out", "-o", help="Where to save (default: ./ronin_image_<ts>.png)."),
    backend: str = typer.Option("pollinations", "--backend", help="pollinations (free, no key) | openai (needs OPENAI_API_KEY)."),
    size: str = typer.Option("1024x1024", "--size", help="WIDTHxHEIGHT."),
    seed: int = typer.Option(None, "--seed", help="Seed for reproducible results (pollinations)."),
    model: str = typer.Option(None, "--model", help="Backend model override (e.g. flux, gpt-image-1)."),
    show: bool = typer.Option(True, "--show/--no-show", help="Display in the terminal after generating."),
) -> None:
    """Generate an image from text and show it in the terminal.

    Free by default (Pollinations — no API key). Displays inline on iTerm2, via
    chafa/viu/imgcat if installed, otherwise opens it in your image viewer.
    """
    from .media import display_image, generate_image

    text = " ".join(prompt)
    try:
        width, height = (int(x) for x in size.lower().split("x", 1))
    except ValueError:
        console.print(f"[red]✗[/red] --size must look like 1024x1024 (got '{size}')")
        raise typer.Exit(2)

    with console.status(f"[cyan]drawing[/cyan] [dim]{text[:60]}[/dim] via {backend}…", spinner="dots"):
        try:
            path = generate_image(text, backend=backend, out=out, width=width, height=height,
                                  seed=seed, model=model)
        except Exception as e:  # noqa: BLE001 — surface any backend/network error cleanly
            console.print(f"[red]✗ image generation failed:[/red] {e}")
            raise typer.Exit(1)

    console.print(f"[green]✓[/green] saved [cyan]{path}[/cyan]")
    if show:
        how = display_image(path)
        if how == "none":
            console.print("[dim]couldn't display inline — open the file above to view it. "
                          "(tip: `brew install chafa` for in-terminal rendering)[/dim]")
        elif how == "open":
            console.print("[dim]opened in your image viewer[/dim]")


@app.command()
def video(
    prompt: list[str] = typer.Argument(..., help="What to animate, e.g. \"a red panda surfing a neon wave\"."),
    out: Path = typer.Option(None, "--out", "-o", help="Where to save the mp4 (default: ./ronin_video_<ts>.mp4)."),
    frames: int = typer.Option(12, "--frames", "-n", help="Number of AI-generated frames."),
    fps: int = typer.Option(8, "--fps", help="Frames per second of the output video."),
    backend: str = typer.Option("pollinations", "--backend", help="Per-frame image backend (frames engine): pollinations (free) | openai."),
    engine: str = typer.Option("frames", "--engine", help="frames (free, ffmpeg) | replicate (paid, real-motion; needs REPLICATE_API_TOKEN)."),
    size: str = typer.Option("512x512", "--size", help="WIDTHxHEIGHT (even numbers; frames engine)."),
    seed: int = typer.Option(None, "--seed", help="Base seed; each frame uses seed+i (frames engine)."),
    model: str = typer.Option(None, "--model", help="Model override (frames: image model; replicate: owner/name slug)."),
    show: bool = typer.Option(True, "--show/--no-show", help="Preview first frame inline + open the mp4."),
) -> None:
    """Generate a short video from text and open it.

    Two engines:
    - frames (default, FREE): generates N AI frames (Pollinations, no key) and
      stitches them into a real .mp4 with ffmpeg. Frame-animation, not Sora-grade.
    - replicate (PAID, real-motion): runs a text-to-video model on Replicate.
      Needs REPLICATE_API_TOKEN and costs money per clip.

    You can't play video *in* a terminal, so ronin previews the first frame
    (frames engine) and opens the mp4 in your player.
    """
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

    from .media import (
        DEFAULT_REPLICATE_VIDEO_MODEL,
        display_image,
        ffmpeg_available,
        generate_video,
        generate_video_replicate,
        open_file,
    )

    text = " ".join(prompt)

    # --- paid real-motion path ------------------------------------------------
    if engine == "replicate":
        rep_model = model or DEFAULT_REPLICATE_VIDEO_MODEL
        console.print(f"[dim]generating real-motion video on Replicate "
                      f"([bold]{rep_model}[/bold]) — this costs money and can take minutes…[/dim]")
        with console.status("[cyan]submitting…[/cyan]", spinner="dots") as status:
            def on_status(s: str) -> None:
                status.update(f"[cyan]replicate:[/cyan] {s}…")
            try:
                result = generate_video_replicate(text, out=out, model=rep_model, on_status=on_status)
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]✗ video generation failed:[/red] {e}")
                raise typer.Exit(1)
        console.print(f"[green]✓[/green] saved [cyan]{result.path}[/cyan] [dim](real-motion · {rep_model})[/dim]")
        if show:
            open_file(result.path)
            console.print("[dim]opened the video in your player[/dim]")
        return

    if engine != "frames":
        console.print(f"[red]✗[/red] unknown --engine '{engine}' (choose: frames | replicate)")
        raise typer.Exit(2)

    # --- free frames path -----------------------------------------------------
    if not ffmpeg_available():
        console.print("[red]✗[/red] ffmpeg not found — install it to build videos "
                      "([bold]brew install ffmpeg[/bold]).")
        raise typer.Exit(2)
    try:
        width, height = (int(x) for x in size.lower().split("x", 1))
    except ValueError:
        console.print(f"[red]✗[/red] --size must look like 512x512 (got '{size}')")
        raise typer.Exit(2)

    console.print(f"[dim]generating {frames} frames via {backend} (this is frame-animation, "
                  "not real-motion video)…[/dim]")
    with Progress(
        SpinnerColumn(), TextColumn("[cyan]frame[/cyan] {task.completed}/{task.total}"),
        BarColumn(), console=console, transient=True,
    ) as progress:
        task_id = progress.add_task("frames", total=frames)

        def on_frame(done: int, total: int) -> None:
            progress.update(task_id, completed=done)

        try:
            result = generate_video(text, out=out, frames=frames, fps=fps, width=width,
                                    height=height, seed=seed, backend=backend, model=model,
                                    on_frame=on_frame)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]✗ video generation failed:[/red] {e}")
            raise typer.Exit(1)

    console.print(f"[green]✓[/green] saved [cyan]{result.path}[/cyan] "
                  f"[dim]({result.frames} frames @ {result.fps}fps)[/dim]")
    if show:
        if result.poster is not None:
            console.print("[dim]first frame:[/dim]")
            display_image(result.poster)
        open_file(result.path)
        console.print("[dim]opened the video in your player[/dim]")


@app.command()
def say(
    text: list[str] = typer.Argument(None, help="What to speak."),
    voice: str = typer.Option(None, "--voice", "-v", help="Voice name (see --list-voices)."),
    rate: int = typer.Option(None, "--rate", "-r", help="Words per minute."),
    out: Path = typer.Option(None, "--out", "-o", help="Save audio to a file instead of speaking aloud."),
    list_voices: bool = typer.Option(False, "--list-voices", help="List installed voices and exit."),
) -> None:
    """Speak text aloud — or save it as an audio file with --out.

    Free, no API key: uses your OS speech engine (macOS `say`, Linux espeak).
    """
    from .audio import list_voices as _list_voices
    from .audio import speak, tts_engine

    if list_voices:
        voices = _list_voices()
        if not voices:
            console.print("[dim]no voices found (macOS only for now)[/dim]")
        else:
            console.print("[bold]voices[/bold] [dim](use with --voice)[/dim]")
            console.print("  " + ", ".join(voices))
        return

    if tts_engine() is None:
        console.print("[red]✗[/red] no text-to-speech engine "
                      "(macOS has `say`; on Linux install [bold]espeak-ng[/bold]).")
        raise typer.Exit(2)
    if not text:
        console.print("[red]✗[/red] give me something to say, e.g. [cyan]ronin say \"hello\"[/cyan]")
        raise typer.Exit(2)

    spoken = " ".join(text)
    try:
        path = speak(spoken, voice=voice, rate=rate, out=out)
    except RuntimeError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)
    if path is not None:
        console.print(f"[green]✓[/green] saved audio to [cyan]{path}[/cyan]")
    else:
        console.print("[green]✓[/green] [dim]spoke aloud[/dim]")


@app.command()
def see(
    image: Path = typer.Argument(..., help="Path to a local image (png/jpg/gif/webp)."),
    question: list[str] = typer.Argument(None, help="What to ask about it (default: describe it)."),
) -> None:
    """Ask the model about a local image — ronin's eyes.

    Uses your configured provider's vision model (Claude, gpt-4o, etc.).
    Text-only models can't see — switch to a vision-capable model if it refuses.
    """
    from .vision import describe_image

    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] vision needs a provider key. Run [bold]ronin set-key[/bold] "
                      f"(provider: [bold]{config.provider}[/bold]).")
        raise typer.Exit(2)
    q = " ".join(question) if question else None
    # show the image inline first (nice for the demo), then the answer
    try:
        from .media import display_image
        display_image(image)
    except Exception:  # noqa: BLE001
        pass
    with console.status("[cyan]looking…[/cyan]", spinner="dots"):
        try:
            answer = describe_image(config, image, q)
        except FileNotFoundError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(2)
        except Exception as e:  # noqa: BLE001
            from .runner import _friendly_provider_error
            console.print(_friendly_provider_error(e, config))
            raise typer.Exit(1)
    console.print(Panel(answer or "[dim](no answer)[/dim]", title=f"👁  {image.name}", border_style="green", padding=(1, 2)))


@app.command()
def explain(
    target: str = typer.Argument(..., help="File, directory, or repo path to explain."),
    question: list[str] = typer.Argument(None, help="Optional focus, e.g. 'how does auth work'."),
    diagram: bool = typer.Option(True, "--diagram/--no-diagram", help="Also generate a Mermaid architecture diagram."),
    speak: bool = typer.Option(False, "--speak", help="Narrate the explanation aloud (text-to-speech)."),
    out: Path = typer.Option(None, "--out", "-o", help="Write the explanation + diagram to a Markdown file."),
    root: Path = typer.Option(Path("."), "--root", help="Project root the paths are relative to."),
    max_steps: int = typer.Option(15, "--max-steps", help="Iteration cap."),
) -> None:
    """Explain a codebase — in plain English, with an auto-generated architecture
    diagram, and optionally narrated aloud. Onboard to any repo in minutes.

    A pure coding agent explains; ronin also *draws* it (Mermaid) and *speaks* it.
    Read-only — it explores and explains, never edits.
    """
    from .agent_mode import has_real_key
    from .explain_mode import run_explain, strip_code_blocks

    config = load_config()
    if not has_real_key(config):
        console.print(f"[red]✗[/red] explain needs an LLM key. Run [bold]ronin set-key[/bold] "
                      f"(provider: [bold]{config.provider}[/bold]).")
        raise typer.Exit(2)

    q = " ".join(question) if question else None
    console.print(Panel.fit(f"[bold]explaining[/bold] [cyan]{target}[/cyan]"
                            + (f"\n[dim]focus: {q}[/dim]" if q else ""),
                            border_style="cyan", title="ronin explain"))

    result = run_explain(config, target, q, root=root, diagram=diagram, console=console, max_iterations=max_steps)
    console.print()
    if result.blocked:
        console.print(f"[red]✗[/red] {result.output}")
        raise typer.Exit(2)
    if not result.streamed:
        console.print(Panel(result.output or "[dim](no explanation)[/dim]", title="Explanation", border_style="green", padding=(1, 2)))

    if result.mermaid:
        console.print("\n[bold]Architecture diagram[/bold] [dim](Mermaid — renders on GitHub):[/dim]")
        console.print(Panel(f"```mermaid\n{result.mermaid}\n```", border_style="#2dd4bf"))

    if out:
        md = f"# Explanation: {target}\n\n{result.output}\n"
        out.write_text(md, encoding="utf-8")
        console.print(f"[green]✓[/green] wrote [cyan]{out}[/cyan]")

    if speak:
        from .audio import speak as _speak
        from .audio import tts_engine
        if tts_engine() is None:
            console.print("[dim](no text-to-speech engine — skipping --speak)[/dim]")
        else:
            console.print("[dim]🔊 narrating…[/dim]")
            try:
                _speak(strip_code_blocks(result.output)[:1200])
            except RuntimeError:
                pass

    meta = f"iterations: {result.iterations}"
    if result.usage:
        meta += f" · in: {result.usage.get('input_tokens', 0)} · out: {result.usage.get('output_tokens', 0)}"
    console.print(f"[dim]{meta}[/dim]")


def _print_result(result: AgentResultRich, *, raw: bool) -> None:
    if raw:
        sys.stdout.write(result.output + "\n")
        return

    console.print()
    from rich.markdown import Markdown
    body = Markdown(result.output) if result.output else "[dim](empty)[/dim]"
    console.print(Panel(body, title="Answer", border_style="green", padding=(1, 2)))
    if result.trace:
        table = Table(title=f"Trace ({len(result.trace)} steps)", box=box.SIMPLE, show_lines=False)
        table.add_column("kind", style="bold yellow", no_wrap=True)
        table.add_column("content", overflow="fold")
        for step in result.trace:
            content = step["content"]
            if not isinstance(content, str):
                import json as _json
                content = _json.dumps(content, indent=2, default=str)
            table.add_row(step["kind"], content[:600])
        console.print(table)
    meta = f"iterations: {result.iterations}"
    if result.usage:
        meta += f" · in: {result.usage.get('input_tokens', 0)} · out: {result.usage.get('output_tokens', 0)}"
    if result.demo_mode:
        meta += " · [yellow]demo mode[/yellow]"
    console.print(f"[dim]{meta}[/dim]")


if __name__ == "__main__":  # pragma: no cover
    app()
