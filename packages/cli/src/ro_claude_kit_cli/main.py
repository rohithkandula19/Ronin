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
from .config import PROVIDER_PRESETS, CSKConfig, find_config_path, load_config, save_config
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


# --------------------------------------------------------------------------
# Panda mascot — a little animated panda that *does things* on launch.
#
# Each activity is a list of frames; each frame is a list of text lines. The
# head is the round-eared bear face ``ʕ•ᴥ•ʔ`` — it renders identically in any
# terminal (plain text, no colour tricks, no block-art striping), and the
# motion between frames is what sells "panda running / dancing / …".
# --------------------------------------------------------------------------

_PANDA_NEUTRAL = [
    " ʕ•ᴥ•ʔ ",
    "  (   ) ",
    "  ‾‾‾‾  ",
]

_PANDA_ACTIVITIES: dict[str, list[list[str]]] = {
    "dancing": [
        [" ♪ \\ʕ•ᴥ•ʔ/", "    (   )  ", "   _/   \\_ "],
        ["    ƪʕ•ᴥ•ʔʅ ♪", "    (   )  ", "    \\   /  "],
        ["     \\ʕ•ᴥ•ʔ/ ♪", "    (   )  ", "   _/   \\_ "],
        [" ♪  ƪʕ•ᴥ•ʔʅ ", "    (   )  ", "    \\   /  "],
    ],
    "running": [
        [" »   ʕ•ᴥ•ʔ ", "    ε(   )϶", "     /   ⌐ "],
        [" »»  ʕ•ᴥ•ʔ ", "    ε(   )϶", "     ⌐   \\ "],
        [" »   ʕ•ᴥ•ʔ ", "    ε(   )϶", "     J   L "],
        [" »»  ʕ•ᴥ•ʔ ", "    ε(   )϶", "     L   J "],
    ],
    "playing": [
        [" ʕ•ᴥ•ʔﾉ      ●", "  (   )  ", "  /   \\  "],
        [" ʕ•ᴥ•ʔﾉ   ●  ", "  (   )  ", "  /   \\  "],
        [" ʕ•ᴥ•ʔﾉ ●   ", "  (   )  ", "  /   \\  "],
        [" ʕ•ᴥ•ʔﾉ●     ", "  (   )  ", "  /   \\  "],
    ],
    "playing football": [
        [" ʕ•ᴥ•ʔ     ", "  (   )    ", "  /  L ●   "],
        [" ʕ•ᴥ•ʔ  ●  ", "  (   )    ", "  /   ⌐    "],
        [" ʕ•ᴥ•ʔ    ●", "  (   )    ", "  /   \\    "],
        [" ʕ•ᴥ•ʔ  ●  ", "  (   )    ", "  /   ⌐    "],
    ],
    "sleeping": [
        [" ʕ-ᴥ-ʔ   z ", "  (   )  ", "  ‾‾‾‾‾  "],
        [" ʕ-ᴥ-ʔ  Z  ", "  (   )  ", "  ‾‾‾‾‾  "],
        [" ʕ-ᴥ-ʔ zZ  ", "  (   )  ", "  ‾‾‾‾‾  "],
        [" ʕ-ᴥ-ʔ Z   ", "  (   )  ", "  ‾‾‾‾‾  "],
    ],
}


def _normalize_frames(frames: list[list[str]]) -> list[list[str]]:
    """Pad every frame to the same line-count and width so the panda doesn't
    jitter or resize the panel as it animates."""
    rows = max(len(f) for f in frames)
    width = max((len(line) for f in frames for line in f), default=0)
    out = []
    for f in frames:
        padded = [line.ljust(width) for line in f]
        padded += [" " * width] * (rows - len(padded))
        out.append(padded)
    return out


def _panda_panel(frame_lines: list[str], caption: str):
    """Wrap one mascot frame + the ronin wordmark in a centred panel."""
    from rich.panel import Panel
    from rich.align import Align

    mascot = "\n".join(f"[bold white]{line}[/bold white]" for line in frame_lines)
    body = (
        mascot
        + f"\n\n[bold magenta]ronin[/bold magenta] [dim]v{__version__}[/dim]"
        + f"  ·  [dim]{caption}[/dim]\n"
        + "[dim]briefing · agent · code · chat · tui[/dim]"
    )
    return Panel.fit(Align.center(body), border_style="magenta", padding=(1, 3))


def _banner(animate: bool = True, activity: str | None = None, loops: int = 2) -> None:
    """ronin's panda — a small panda mascot doing a (random) activity on launch.

    On a real TTY it animates frame-by-frame; piped/non-interactive output gets
    a single still frame so logs and tests stay clean and deterministic.
    """
    import random
    import sys
    import time

    name = activity or random.choice(list(_PANDA_ACTIVITIES))
    frames = _normalize_frames(_PANDA_ACTIVITIES[name])
    caption = f"masterless Claude agent · {name}"

    if animate and sys.stdout.isatty():
        from rich.live import Live
        with Live(console=console, refresh_per_second=12, transient=False) as live:
            for _ in range(loops):
                for f in frames:
                    live.update(_panda_panel(f, caption))
                    time.sleep(0.14)
            live.update(_panda_panel(frames[0], caption))
    else:
        console.print(_panda_panel(_normalize_frames([_PANDA_NEUTRAL])[0],
                                   "masterless Claude agent"))


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return

    import sys
    # Non-interactive (pipe/test) → just show help and exit, cleanly (no banner).
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        console.print(ctx.get_help())
        return

    # Animated panda mascot doing an activity — dancing / running / playing /
    # sleeping — in a panel on launch. It actually *moves*, frame by frame.
    _banner()

    # Interactive terminal: drop into a session, the way `claude` does.
    config = load_config()
    if not config.has_provider_auth():
        console.print(
            "[dim]No provider configured yet. Run [bold]ronin init[/bold] "
            "(or [bold]ronin init --demo[/bold]) to get started, or [bold]ronin --help[/bold] for all commands.[/dim]"
        )
        console.print(ctx.get_help())
        return

    # Bare `ronin` = one assistant that does everything: talk, generate media,
    # AND read/edit/run code (edits + commands gated). `ronin chat` is the
    # narrower talk/media surface; `ronin code` is the pure coding agent.
    console.print(
        "[dim]One assistant for everything — talk, [bold]\"generate a panda image\"[/bold], "
        "[bold]\"write code to …\"[/bold] (edits need approval), query your data, and more.\n"
        "[bold]@path[/bold] to reference files · [bold]/help[/bold] for commands · "
        "[bold]/q[/bold] to quit.[/dim]\n"
    )
    from .code_mode import run_unified_session
    run_unified_session(config, root=Path("."), console=console)


def _is_code_project(path: Path) -> bool:
    """Heuristic: does ``path`` look like a code repository? (decides whether bare
    `ronin` opens the coding agent vs. the data/media chat)."""
    markers = (
        ".git", "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
        "pom.xml", "build.gradle", "Gemfile", "RONIN.md", "CLAUDE.md", "AGENTS.md",
    )
    return any((path / m).exists() for m in markers)


# ---------- init ----------

@app.command()
def init(
    demo: bool = typer.Option(False, "--demo", help="Skip credential prompts; use built-in demo data."),
    scope: str = typer.Option("project", "--scope", help="'project' (./.csk/) or 'user' (~/.config/csk/)."),
    yes: bool = typer.Option(False, "-y", "--yes", help="Overwrite existing config without confirmation."),
) -> None:
    """Create a ronin config file."""
    existing = find_config_path()
    if existing and not yes:
        if not Confirm.ask(f"[yellow]A config already exists at[/yellow] {existing}. Overwrite?", default=False):
            console.print("[dim]aborted[/dim]")
            raise typer.Exit(1)

    if demo:
        cfg = CSKConfig(demo_mode=True)
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
        "you want it to query.\n[dim]Values are stored in plaintext at .csk/config.toml — .gitignore "
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
            f"[bold]{default_model}[/bold] instead. (Pass --model or edit .csk/config.toml to change it.)"
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

    cfg = CSKConfig(
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
    provider: str = typer.Option(None, "--provider", help="Set/override the provider (e.g. groq, anthropic)."),
    scope: str = typer.Option("project", "--scope", help="'project' (./.csk/) or 'user' (~/.config/csk/)."),
) -> None:
    """Set just the LLM API key — masked input with a length/preview confirmation
    so you can tell the paste actually worked (no more blind double-pasting)."""
    config = load_config()
    if provider:
        config.provider = provider
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
    if prov == "anthropic":
        config.anthropic_api_key = key
    else:
        config.openai_api_key = key
    path = save_config(config, scope=scope)
    console.print(f"[green]✓[/green] key saved for [bold]{prov}[/bold] → [cyan]{path}[/cyan]")
    console.print("[dim]verify it works: [bold]ronin doctor --check[/bold][/dim]")


# ---------- ask ----------

@app.command()
def ask(
    question: list[str] = typer.Argument(..., help="The question to ask. Wrap multi-word questions in quotes."),
    raw: bool = typer.Option(False, "--raw", help="Print plain output instead of rich panels."),
) -> None:
    """One-shot: send a question to Claude with your configured tools, print answer + trace."""
    config = load_config()
    if not config.has_provider_auth():
        console.print(
            f"[red]✗[/red] No credentials for provider [bold]{config.provider}[/bold]. "
            "Run [bold]ronin init[/bold] (or [bold]ronin init --demo[/bold]) first."
        )
        raise typer.Exit(2)

    text = " ".join(question)
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

def _provider_live_check(config: CSKConfig) -> str:
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

eval_app = typer.Typer(help="Eval suite: golden datasets, judge runs, drift detection.")
app.add_typer(eval_app, name="eval")


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
    from ro_claude_kit_eval_suite.cli import main as eval_main

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
    from ro_claude_kit_eval_suite.cli import main as eval_main

    argv = ["drift", str(baseline), str(candidate), "--threshold", str(threshold)]
    raise typer.Exit(eval_main(argv))


# ---------- plugins ----------

@app.command()
def plugins() -> None:
    """List user plugins loaded from .csk/plugins/."""
    from .plugins import load_plugins

    results = load_plugins()
    if not results:
        console.print(
            "[dim]no plugins. Drop a Python file in[/dim] [bold].csk/plugins/[/bold] "
            "[dim]exporting[/dim] [bold]register_tools() -> list[Tool][/bold]."
        )
        return
    table = Table(title="Loaded plugins", box=box.ROUNDED)
    table.add_column("plugin", style="cyan", no_wrap=True)
    table.add_column("tools", overflow="fold")
    table.add_column("status", style="dim", overflow="fold")
    for r in results:
        tools_str = ", ".join(t.name for t in r.tools) or "[dim](none)[/dim]"
        status = "[green]ok[/green]" if r.error is None else f"[red]error:[/red] {r.error}"
        table.add_row(r.name, tools_str, status)
    console.print(table)


# ---------- serve ----------

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Expose the configured agent as a REST API (POST /ask + GET /health)."""
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for provider [bold]{config.provider}[/bold]. Run [bold]ronin init[/bold] first.")
        raise typer.Exit(2)

    import uvicorn
    from .server import make_app

    console.print(Panel.fit(
        f"[bold cyan]ronin serve[/bold cyan] — agent over HTTP\n"
        f"[dim]provider:[/dim] {config.provider} ({config.resolved_model()})\n"
        f"[dim]listening:[/dim] http://{host}:{port}\n\n"
        f"POST /ask     {{\"question\": \"...\"}}\n"
        f"GET  /health",
        border_style="cyan",
    ))
    app_fastapi = make_app(config)
    uvicorn.run(app_fastapi, host=host, port=port, log_level="info")


# ---------- costs ----------

@app.command()
def costs(
    by: str = typer.Option("model", "--by", help="Group by: model | day | none"),
) -> None:
    """Show token usage + estimated USD costs from .csk/usage.jsonl."""
    from .usage import load_records, summarize, usage_path

    path = usage_path()
    records = load_records()
    if not records:
        console.print(
            f"[dim]no usage recorded yet at[/dim] [bold]{path}[/bold]. "
            "[dim]Run[/dim] [bold]ronin ask[/bold] [dim]with a real provider to start tracking.[/dim]"
        )
        return
    summary = summarize(records)

    header = Table(title="Total usage", box=box.ROUNDED, show_header=False)
    header.add_column(style="cyan")
    header.add_column()
    header.add_row("calls", str(summary.total_calls))
    header.add_row("input tokens", f"{summary.total_input_tokens:,}")
    header.add_row("output tokens", f"{summary.total_output_tokens:,}")
    header.add_row("estimated cost", f"${summary.total_cost_usd:.4f}")
    console.print(header)

    if by in ("model", "day"):
        breakdown = summary.by_model if by == "model" else summary.by_day
        if breakdown:
            t = Table(title=f"By {by}", box=box.ROUNDED)
            t.add_column(by, style="cyan")
            t.add_column("calls", justify="right")
            t.add_column("input", justify="right")
            t.add_column("output", justify="right")
            t.add_column("cost (USD)", justify="right")
            for key, s in sorted(breakdown.items()):
                t.add_row(
                    key, str(s.total_calls),
                    f"{s.total_input_tokens:,}", f"{s.total_output_tokens:,}",
                    f"${s.total_cost_usd:.4f}",
                )
            console.print(t)


# ---------- plugins ----------

@app.command()
def plugins() -> None:
    """Discover and list user plugins from .csk/plugins/."""
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


# ---------- costs ----------

@app.command()
def costs(
    by: str = typer.Option("model", "--by", help="Group by 'model' or 'day'."),
) -> None:
    """Show token + cost usage recorded by previous ronin commands."""
    from .usage import load_records, summarize, usage_path

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
    template: Optional[Path] = typer.Option(None, "--template", help="Path to a briefing-template.toml. Defaults to .csk/briefing-template.toml if present."),
    history: bool = typer.Option(False, "--history", help="Show a trend table of past briefings instead of running a new one."),
    no_save: bool = typer.Option(False, "--no-save", help="Don't persist this run to .csk/briefings/."),
) -> None:
    """Weekly founder briefing: revenue, churn, payment failures, top engineering issues.

    Works offline in demo mode; uses Claude (or your configured provider) in real mode.
    Output is Markdown — paste into Slack, email, or a doc.

    Each run is auto-saved to ``.csk/briefings/<date>.json`` so subsequent runs can
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
                "[dim]no briefing history yet at[/dim] [cyan].csk/briefings/[/cyan][dim]. "
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

    # If a template TOML is configured (--template flag or .csk/briefing-template.toml),
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
        console.print("[bold magenta]📋 planning (read-only)…[/bold magenta]")
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

    Memory is persistent and user-global (~/.csk/memory.json): ronin recalls
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
    body = "\n".join(f"[#c678dd]•[/#c678dd] {m['text']}" for m in mems)
    console.print(Panel(body, title=f"🧠 {len(mems)} thing(s) ronin remembers about you",
                        border_style="#c678dd", padding=(1, 2)))
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
        _banner(activity=name, loops=loops)


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
        console.print(Panel(f"```mermaid\n{result.mermaid}\n```", border_style="magenta"))

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
