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
from typing import List, Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import __version__, display_version
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

# Grouped sub-apps — the wedge front page stays ~10 verbs; everything else
# lives under `ronin dev …` (repo/code workflows) and `ronin util …` (the rest).
from .dev_cmds import dev_app
from .util_cmds import util_app
from . import telegram_cmds  # noqa: F401 — registers `ronin util telegram`

app.add_typer(dev_app, name="dev")
app.add_typer(util_app, name="util")


# The panda mascot (dancing / running / playing / playing football / sleeping)
# lives in panda_art.py — single source of truth for both the launch banner
# and the `ronin panda` command. We re-export the activities dict so existing
# imports keep working.
from .panda_art import PANDA_ACTIVITIES as _PANDA_ACTIVITIES, render_panda as _render_panda


def _version_callback(value: bool) -> None:
    """Eager --version: print the version and exit (before any subcommand runs)."""
    if value:
        from .selfupdate import version_line
        console.print(version_line())
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show the ronin version and exit.",
    ),
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
    budget: float = typer.Option(
        None, "--budget",
        help="Spend cap (USD) for this session — ronin warns once estimated cost "
             "crosses it. The footer shows 💸 spent $X of $cap.",
    ),
    fast: bool = typer.Option(
        False, "--fast",
        help="Snappier turns: ship only the core coding tools (smaller per-call "
             "payload → less latency + fewer rate-limit hits). Trades breadth for speed.",
    ),
    effort: str = typer.Option(
        None, "--effort",
        help="Reasoning budget: low|medium|high|xhigh — maps to each provider's native knob.",
    ),
    faithfulness: str = typer.Option(
        None, "--faithfulness",
        help="Faithfulness edit guard: score every proposed write/edit against the "
             "files the agent read. 'warn' surfaces an ungrounded-edit score; 'gate' "
             "holds an ungrounded edit for revision (even under --full-access).",
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

    # `--tui` is an explicit request for the full-screen app and must launch even
    # when stdout is not a plain tty (terminal multiplexers, IDE terminals, and
    # some launch shims wrap or pipe stdout). Previously the non-interactive guard
    # below printed help and returned FIRST, so `ronin --tui` exited instantly back
    # to the shell whenever stdout was not a tty. The TUI only needs a readable
    # keyboard (stdin); Textual itself degrades gracefully if the terminal is
    # truly unusable. So dispatch the TUI here, before the help fallback.
    if tui and not no_tui and sys.stdin.isatty():
        config = load_config()
        if not _ensure_tui_auth(config):
            return
        from .tui import run_tui
        run_tui(config=config, root=str(Path(".")))
        return

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
        import sys
        # Interactive TTY → a friendly guided picker; otherwise the help fallback.
        if sys.stdin.isatty() and sys.stdout.isatty():
            from .onboard import onboard
            picked = onboard(console, config)
            if picked is not None:
                config = picked
            else:
                from .onboard import render_first_run
                render_first_run(console)
                return
        else:
            from .onboard import render_first_run
            render_first_run(console)
            console.print(ctx.get_help())
            return

    if full_access:
        config = config.model_copy(update={"full_access": True})
        console.print("[bold #e0af68]⚠ FULL-ACCESS MODE[/bold #e0af68] [dim]— filesystem-wide, "
                      "auto-approving every edit & command, no sandbox. Use only in a directory "
                      "you trust.[/dim]")
        console.print("[dim]  (a destructive floor still stands: rm -rf / force-push / drop table "
                      "/ mkfs need a typed confirmation, even here.)[/dim]")
    if not isinstance(sentinel, bool):
        sentinel = False
    if sentinel:
        config = config.model_copy(update={"sentinel": True})
        console.print("[#6b7089]🛡 sentinel mode — the agent abstains over bluffing[/#6b7089]")
    if isinstance(budget, (int, float)) and budget > 0:
        config = config.model_copy(update={"budget": float(budget)})
        console.print(f"[#6b7089]💸 budget cap — ${budget:.2f} for this session[/#6b7089]")
    if not isinstance(fast, bool):
        fast = False
    if fast:
        config = config.model_copy(update={"fast": True})
        console.print("[#6b7089]⚡ fast mode — core tools only, snappier turns[/#6b7089]")
    if isinstance(effort, str) and effort.strip():
        from ronin_agent_patterns import describe_effort, normalize_effort
        _eff = normalize_effort(effort)
        if _eff is not None:
            config = config.model_copy(update={"effort": _eff})
            console.print(f"[#6b7089]🧠 effort [bold]{_eff}[/bold] — {describe_effort(_eff)}[/#6b7089]")
        else:
            console.print(f"[#e0af68]unknown --effort {effort!r}; use low|medium|high|xhigh[/#e0af68]")
    if isinstance(faithfulness, str) and faithfulness.strip():
        _fmode = faithfulness.strip().lower()
        if _fmode in ("off", "warn", "gate"):
            config = config.model_copy(update={"faithfulness": _fmode})
            if _fmode != "off":
                console.print(f"[#6b7089]🔎 faithfulness {_fmode} - edits scored against the "
                              "files the agent read[/#6b7089]")
        else:
            console.print(f"[#e0af68]unknown --faithfulness mode {faithfulness!r}; "
                          "use off|warn|gate[/#e0af68]")

    root = Path(".")

    # The full-screen `ronin --tui` app is dispatched at the top of this callback
    # (before the non-tty help fallback) so it launches even when stdout is wrapped
    # or piped. The default below is the minimal, Claude-Code-style INLINE REPL —
    # output flows in the terminal's normal scrollback under a tiny logo and a
    # bordered input box (no full-screen takeover, no side panes).
    from .code_mode import run_code_session, run_unified_session

    if _is_code_project(root):
        if not full_access:
            console.print("[dim]code project · reads run free, edits & commands need approval[/dim]")
        run_code_session(config, root=root, console=console, yolo=full_access)
        return

    # Outside a code repo = one assistant that does everything: talk, generate
    # media, query data, write/run code when asked.
    run_unified_session(config, root=root, console=console, yolo=full_access)


def _ensure_tui_auth(config: RoninConfig) -> bool:
    """Make sure the TUI has a usable provider before it takes over the screen.

    Returns True if we can launch. When no provider is configured we run the same
    guided onboard picker the REPL uses; if the user skips it, we print the setup
    hint and return False so the caller bails cleanly instead of opening an
    unusable full-screen app."""
    if config.has_provider_auth():
        return True
    from .onboard import onboard
    picked = onboard(console, config)
    if picked is not None and picked.has_provider_auth():
        config.__dict__.update(picked.__dict__)
        return True
    from .theme import ACCENT
    console.print(f"\n[bold {ACCENT}]ʕ•ᴥ•ʔ  ronin[/bold {ACCENT}]\n")
    console.print("[dim]Set up any time with [bold]ronin init[/bold], then run "
                  "[bold]ronin --tui[/bold] again.[/dim]")
    return False


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

    console.print("[dim]  local = the embedded in-process model — zero keys, zero egress; "
                  "with RONIN_ADAPTER set it runs ronin-code-1.5b, ronin's own fine-tune.[/dim]")
    provider_choice = Prompt.ask(
        "LLM provider",
        choices=["anthropic", "local", "ollama", "openai", "together", "groq", "fireworks", "custom"],
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
    elif provider_choice not in ("anthropic", "ollama", "local"):
        base_url = preset.get("base_url") or ""

    anthropic_key: str | None = None
    openai_key: str | None = None
    if provider_choice == "anthropic":
        anthropic_key = Prompt.ask(
            "ANTHROPIC_API_KEY",
            default=os.environ.get("ANTHROPIC_API_KEY") or "",
            password=True,
        ) or None
    elif provider_choice not in ("ollama", "local"):
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


@util_app.command("set-key")
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
    json_out: bool = typer.Option(
        False, "--json",
        help="Emit ONE JSON object on stdout: {result, tool_calls, files_changed, "
             "cost, duration, success, usage}. Progress/logs go to stderr — for CI "
             "and scripting.",
    ),
) -> None:
    """One-shot Q&A — also reads piped stdin, so ronin composes in shell pipelines.

    Examples:
      ronin ask "which customers churned?"
      cat error.log | ronin ask "what's the root cause?"
      git diff | ronin ask "write release notes for these changes"
      ronin ask --json "list the open ports"        # machine-readable, for scripts
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

    if json_out:
        import json as _json
        import time as _time
        _t0 = _time.monotonic()
        # console=None keeps stdout clean; only the JSON object is written there.
        result = run_ask(config, text, console=None)
        tool_calls = [
            s.get("content") for s in (result.trace or [])
            if isinstance(s, dict) and s.get("kind") in ("tool_call", "action")
        ]
        payload = {
            "result": result.output,
            "success": result.success,
            "tool_calls": tool_calls,
            "files_changed": [],  # `ask` is read-only Q&A; edits happen in `code`
            "cost": None,          # not priced here; token counts are in `usage`
            "usage": result.usage or {},
            "duration": round(_time.monotonic() - _t0, 3),
            "error": result.error,
        }
        print(_json.dumps(payload))
        raise typer.Exit(0 if result.success else 1)

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

@util_app.command()
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


@util_app.command()
def pipeline(
    task: str = typer.Argument("", help="The task to run through the role pipeline (omit with --resume)."),
    roles: str = typer.Option(
        None, "--roles", help="Comma-separated role order. Default: "
        "architect,implementer,reviewer,tester,verifier."),
    verify_cmd: Optional[List[str]] = typer.Option(None, "--verify-cmd", help="Verification command the verifier runs itself (gated). Repeatable for multiple suites."),
    verify_suite: Optional[List[str]] = typer.Option(None, "--verify-suite", help="Named verification suite 'name:command' (gated). Append '?' to the name for optional (e.g. 'lint?:...'). Repeatable."),
    required_suite: Optional[List[str]] = typer.Option(None, "--required-suite", help="Required suite 'name:command' — its failure fails the run (gated). Repeatable."),
    optional_suite: Optional[List[str]] = typer.Option(None, "--optional-suite", help="Optional suite 'name:command' — its failure only warns (gated). Repeatable."),
    auto_verify_all: bool = typer.Option(False, "--auto-verify-all", help="Auto-detect and run multiple suites (tests/build required, lint/typecheck optional) — all gated."),
    verify_timeout: int = typer.Option(600, "--verify-timeout", help="Timeout (seconds) for the verify command."),
    no_independent_verify: bool = typer.Option(False, "--no-independent-verify", help="Skip running --verify-cmd; keep advisory verifier only."),
    no_auto_verify: bool = typer.Option(False, "--no-auto-verify", help="Don't auto-detect a verify command when --verify-cmd is absent."),
    semantic_contract: bool = typer.Option(False, "--semantic-contract/--no-semantic-contract", help="Run a read-only semantic check: does the diff actually fulfil the plan? (extra model call)"),
    diff_context: int = typer.Option(3, "--diff-context", help="Unified-diff context lines for the captured evidence."),
    max_diff_bytes: int = typer.Option(20000, "--max-diff-bytes", help="Truncate the captured diff excerpt to this many bytes."),
    no_diff_evidence: bool = typer.Option(False, "--no-diff-evidence", help="Don't capture the working-tree diff as evidence."),
    save_state_path: Optional[Path] = typer.Option(None, "--save-state", help="Write PipelineState JSON after every stage."),
    resume: Optional[Path] = typer.Option(None, "--resume", help="Resume a saved PipelineState JSON from the first incomplete stage."),
    rerun_completed: bool = typer.Option(False, "--rerun-completed", help="On --resume, re-run completed stages too."),
    force_resume: bool = typer.Option(False, "--force-resume", help="Resume even if the git/working-tree state changed since the checkpoint."),
    checkpoint: bool = typer.Option(False, "--checkpoint", help="Create a lightweight git safety snapshot before running (never touches your tree)."),
    restore_checkpoint: bool = typer.Option(False, "--restore-checkpoint", help="On an unsafe --resume, offer to restore the run's saved checkpoint (gated, overwrites the tree)."),
    restore_checkpoint_id: Optional[int] = typer.Option(None, "--restore-checkpoint-id", help="Restore a specific checkpoint id on an unsafe --resume (gated)."),
    restore_latest_checkpoint: bool = typer.Option(False, "--restore-latest-checkpoint", help="Restore the newest available checkpoint on an unsafe --resume (gated)."),
    restore_checkpoint_interactive: bool = typer.Option(False, "--restore-checkpoint-interactive", help="On an unsafe --resume, list checkpoints and pick one to restore (gated)."),
    list_checkpoints_flag: bool = typer.Option(False, "--list-checkpoints", help="List available checkpoints (id, created, sha, files) and, with --resume, offer to pick one."),
    no_restore_offer: bool = typer.Option(False, "--no-restore-offer", help="On an unsafe --resume, don't mention/offer checkpoint restore."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan + permissions; run nothing, edit nothing."),
    write: bool = typer.Option(False, "--write", help="Allow doer stages to edit/run (still approval-gated). Off = read-only proposal."),
    free: bool = typer.Option(False, "--free", help="Prefer a free ($0) provider for all stages."),
    offline: bool = typer.Option(False, "--offline", help="Local/Ollama only; strip network tools."),
    json_out: bool = typer.Option(False, "--json", help="Emit the pipeline state as JSON (suppresses the pretty render)."),
    out: Optional[Path] = typer.Option(None, "--out", help="Write the JSON pipeline state to a file."),
    do_commit: bool = typer.Option(False, "--commit", help="After a passing verifier, offer a gated commit (asks first)."),
    do_pr: bool = typer.Option(False, "--pr", help="After committing, offer a gated push + PR (asks first)."),
    branch: Optional[str] = typer.Option(None, "--branch", help="Create this branch before committing."),
    commit_message: Optional[str] = typer.Option(None, "--commit-message", help="Use this commit message instead of drafting one."),
    pr_title: Optional[str] = typer.Option(None, "--pr-title", help="Use this PR title instead of drafting one."),
    pr_body: Optional[str] = typer.Option(None, "--pr-body", help="Use this PR body instead of drafting one."),
) -> None:
    """Run a gated role-handoff pipeline: architect → implementer → reviewer → tester.

    SEQUENTIAL, one agent per stage — NOT parallel and NOT autonomous. Each stage
    wears a role (Wave 3) and hands its summary to the next. Read-only roles
    (architect/reviewer) are enforced; without --write the whole run is a
    read-only proposal; every edit/command still hits the approval gate. A
    blocked/failed stage halts the run.

      ronin pipeline "add CSV export"
      ronin pipeline "fix failing auth tests" --roles debugger,implementer,tester
      ronin pipeline "review this refactor" --roles reviewer,tester --dry-run
      ronin pipeline "add retry logic" --free
      ronin pipeline "analyze this repo" --offline --roles architect,researcher
    """
    from .pipeline import parse_roles, render_pipeline_result, run_pipeline

    config = load_config()

    # --resume: load a saved state and continue; otherwise parse the role order.
    resume_state = None
    if resume is not None:
        from .pipeline_state_io import PipelineStateError, load_state
        try:
            resume_state = load_state(resume)
        except PipelineStateError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2)
        role_list = resume_state.roles
        task = resume_state.task
        # Git-snapshot safety: compare the saved tree state to now; refuse an
        # unsafe resume unless --force-resume. Never resets or stashes anything.
        from .checkpoint import checkpoint_details
        from .checkpoint import restore_checkpoint as _restore
        from .pipeline_git_snapshot import GitSnapshot, compare_snapshots, git_snapshot
        saved_snap = GitSnapshot.model_validate(resume_state.git_snapshot) if resume_state.git_snapshot else None
        details = checkpoint_details(".")

        def _show_checkpoints() -> None:
            if not details:
                console.print("  [dim]no checkpoints found (take one with [bold]--checkpoint[/bold])[/dim]")
                return
            console.print("  [bold]available checkpoints[/bold]")
            for d in details:
                src = " [#6b7089](this run)[/#6b7089]" if d["id"] == resume_state.checkpoint_id else ""
                console.print(f"    [cyan]#{d['id']}[/cyan] [dim]{d['created_at']} · "
                              f"{d['short_sha']} · {d['tracked_files']} files · {d['label']}[/dim]{src}",
                              highlight=False)

        if list_checkpoints_flag:
            _show_checkpoints()

        cmp = compare_snapshots(saved_snap, git_snapshot("."))
        resume_state.resume_git_status = cmp.status if cmp.status != "clean" else "matched"
        if cmp.status in ("changed", "warning"):
            console.print(f"[yellow]⚠ working tree changed since the checkpoint "
                          f"({cmp.status}):[/yellow]")
            for w in cmp.warnings:
                console.print(f"  [yellow]· {w}[/yellow]")

            # Choose which checkpoint to restore (never automatic).
            valid_ids = {d["id"] for d in details}
            target_id = None
            if restore_checkpoint_id is not None:
                if restore_checkpoint_id not in valid_ids:
                    console.print(f"[red]no checkpoint #{restore_checkpoint_id} — see "
                                  "[bold]--list-checkpoints[/bold].[/red]")
                    raise typer.Exit(2)
                target_id = restore_checkpoint_id
            elif restore_latest_checkpoint and details:
                target_id = max(valid_ids)
            elif restore_checkpoint_interactive and details:
                _show_checkpoints()
                pick = Prompt.ask("  restore which checkpoint id? (blank to skip)",
                                  default="", console=console).strip()
                target_id = int(pick) if pick.isdigit() and int(pick) in valid_ids else None
            elif restore_checkpoint and resume_state.checkpoint_id in valid_ids:
                target_id = resume_state.checkpoint_id

            if target_id is not None:
                console.print(f"  [bold]restore checkpoint #{target_id}[/bold] — this "
                              "[bold]overwrites your current working tree[/bold] (files are set to "
                              "the snapshot; files created since are removed).")
                if Confirm.ask("  proceed with restore?", default=False, console=console):
                    console.print(f"  [dim]{_restore('.', target_id)}[/dim]")
                    cmp2 = compare_snapshots(saved_snap, git_snapshot("."))
                    if cmp2.status in ("changed", "warning") and not force_resume:
                        console.print("[red]tree still doesn't match after restore — pass "
                                      "[bold]--force-resume[/bold] to continue anyway.[/red]")
                        raise typer.Exit(2)
                    resume_state.resume_git_status = "restored"
                    resume_state.resume_checkpoint_restore = "restored"
                    console.print("[green]✓ restored — continuing[/green]")
                else:
                    resume_state.resume_checkpoint_restore = "declined"
                    if not force_resume:
                        console.print("[red]restore declined — exiting. Pass [bold]--force-resume[/bold] "
                                      "to continue without restoring.[/red]")
                        raise typer.Exit(2)
            elif details and not no_restore_offer:
                resume_state.resume_checkpoint_restore = "offered"
                console.print("  [dim]checkpoints are available — pass "
                              "[bold]--restore-latest-checkpoint[/bold], "
                              "[bold]--restore-checkpoint-id <n>[/bold], or "
                              "[bold]--restore-checkpoint-interactive[/bold] to restore (gated); "
                              "[bold]--list-checkpoints[/bold] to see them; or "
                              "[bold]--force-resume[/bold] to continue as-is.[/dim]")
                if not force_resume:
                    raise typer.Exit(2)
            else:
                resume_state.resume_checkpoint_restore = "unavailable"
                if not force_resume:
                    console.print("[red]refusing to resume — re-check your changes, then pass "
                                  "[bold]--force-resume[/bold] to continue anyway.[/red]")
                    raise typer.Exit(2)
            if force_resume and resume_state.resume_git_status != "restored":
                console.print("[dim]--force-resume: continuing despite the changes[/dim]")
        elif cmp.status == "unavailable":
            resume_state.resume_git_status = "unavailable"
            console.print("[dim]note: git state unavailable — can't verify the tree is unchanged[/dim]")
    else:
        if not task.strip():
            console.print("[yellow]give a task, or use --resume <file>[/yellow]")
            raise typer.Exit(2)
        try:
            role_list = parse_roles(roles)
        except ValueError as exc:
            console.print(f"[yellow]{exc}[/yellow]")
            raise typer.Exit(2)

    # --checkpoint: a lightweight safety snapshot of the working tree (tracked +
    # untracked) into a ronin ref, WITHOUT touching the index/branch/HEAD. Its id
    # is saved into the state so a later --resume can restore it.
    _checkpoint_id = None
    if checkpoint:
        from .checkpoint import NotAGitRepo, create_checkpoint
        try:
            cp = create_checkpoint(Path("."), label="pipeline")
            _checkpoint_id = cp.id
            console.print(f"[dim]✓ checkpoint #{cp.id} ({cp.sha[:8]}) — restore with "
                          f"[bold]ronin checkpoint restore {cp.id}[/bold] if needed[/dim]")
        except NotAGitRepo:
            console.print("[yellow]--checkpoint needs a git repo — skipping[/yellow]")

    # --json is machine-readable: suppress human rendering (also keeps gates
    # fail-closed since there's no console to approve at).
    render_console = None if json_out else console
    state = run_pipeline(
        config, task, role_list, write=write, free=free, offline=offline,
        dry_run=dry_run, console=render_console,
        verify_cmds=verify_cmd, verify_suites=verify_suite,
        required_suites=required_suite, optional_suites=optional_suite,
        auto_verify_all=auto_verify_all, verify_timeout=verify_timeout,
        independent_verify_enabled=not no_independent_verify,
        auto_verify_enabled=not no_auto_verify,
        semantic_enabled=semantic_contract,
        diff_context=diff_context, max_diff_bytes=max_diff_bytes,
        diff_evidence_enabled=not no_diff_evidence,
        resume_state=resume_state, rerun_completed=rerun_completed,
        save_path=save_state_path, checkpoint_id=_checkpoint_id,
    )

    if json_out:
        typer.echo(state.model_dump_json(indent=2))
    elif not dry_run:
        console.print()
        render_pipeline_result(console, state)

    # Optional, gated commit/PR finish (never silent; honors the dry-run flag).
    if do_commit or do_pr:
        from .pipeline_finish import finish_pipeline
        finish_pipeline(
            None if json_out else console, Path("."), config, state,
            do_commit=do_commit, do_pr=do_pr, branch=branch, message=commit_message,
            title=pr_title, body=pr_body, dry_run=dry_run,
        )

    if out is not None:
        out.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        if not json_out:
            console.print(f"[dim]wrote pipeline state → {out}[/dim]")

    # Dry-run is a preview (nothing ran) → always exit 0. A real run reflects the
    # outcome / combined verdict in the exit code.
    if not dry_run and (state.outcome() in ("failed", "blocked")
                        or state.final_verdict in ("failed", "blocked")):
        raise typer.Exit(1)


@dev_app.command()
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


@util_app.command()
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


@util_app.command()
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


@util_app.command()
def kaizen(
    goal: Optional[str] = typer.Argument(
        None, help="What to improve. Omit to auto-pick the top FIXME/TODO in ronin's own source."),
    tests: Optional[str] = typer.Option(
        None, "--tests", help="Narrow the fitness gate to a path/expression (faster)."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Auto-apply if the fitness gate passes (no prompt)."),
    duel: Optional[str] = typer.Option(
        None, "--duel", help="Have a RIVAL provider[:model] red-team the proven diff before you approve it."),
    continuous: bool = typer.Option(
        False, "--continuous", help="Run one cycle HEADLESS and open a PR for the proven fix (no working-tree "
        "changes). Pair with `ronin schedule` to self-improve nightly."),
) -> None:
    """改善 — ronin sharpens its own blade. Finds a weakness, drafts a fix in an
    isolated git worktree, and PROVES it against the test suite before showing
    you anything. The diff only reaches your working tree if the tests pass.

    Point it at a free provider (`/login cerebras`) and it improves code for $0.
    Something a single-vendor agent structurally won't let you do: an agent that
    edits its own source, with objective eval-proof it worked.

    `--continuous` runs one cycle unattended and opens a PR for the proven fix —
    schedule it (`ronin schedule add nightly-kaizen …`) for an agent that
    measurably improves itself over time, with eval proof, for $0.
    """
    from .kaizen import run_kaizen

    config = load_config()
    if continuous:
        from .kaizen_loop import run_continuous
        res = run_continuous(config, console=console, root=Path("."))
        if res.get("pr_url"):
            console.print(f"[green]改 opened PR → {res['pr_url']}[/green]")
        elif res.get("branch"):
            console.print(f"[green]改 proven fix on branch [bold]{res['branch']}[/bold][/green]")
        else:
            console.print(f"[yellow]改 {res.get('summary') or res.get('note') or 'no improvement this cycle'}[/yellow]")
        return
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

@util_app.command()
def bench(
    models: str = typer.Option(
        ..., "--models", "-m",
        help="Comma-separated provider[:model] specs to benchmark, e.g. "
             "'anthropic,gemini,cerebras,ollama:llama3.1'.",
    ),
    threshold: float = typer.Option(
        0.8, "--threshold", help="Pass-rate bar (0..1) a model must clear to be recommended."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory for durable benchmark evidence."),
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
    try:
        from .model_scorecards import ModelScorecardStore

        saved = ModelScorecardStore(root).record_bench(result)
        if saved:
            console.print(f"[dim]saved {len(saved)} model scorecard(s) under {Path(root) / '.ronin'}[/dim]")
    except OSError as exc:
        console.print(f"[yellow]benchmark evidence was not saved: {exc}[/yellow]")


# ---------- tools ----------

@util_app.command()
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

from dataclasses import dataclass as _dataclass


@_dataclass
class _LiveCheck:
    """Result of the end-to-end provider validation. ``status`` is one row in the
    doctor table; ``remedy`` (if any) is the exact fix printed underneath."""
    status: str
    remedy: str = ""
    ok: bool = False


def _live_models_probe(config: RoninConfig) -> tuple[str | None, list[str]]:
    """Hit the provider's models-list endpoint (the cheap reachability + auth
    probe). Returns ``(error_code, model_ids)`` where ``error_code`` is one of
    ``"auth"`` (401/403), ``"http:<code>"``, ``"unreachable:<Name>"``, or ``None``
    on success. ``model_ids`` is the live id list (empty if the provider doesn't
    expose one — Anthropic, or a 200 with no ``data``)."""
    import urllib.error
    import urllib.request

    provider = config.provider
    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": config.key_for("anthropic") or "", "anthropic-version": "2023-06-01"}
    elif provider == "ollama":
        base = config.resolved_base_url() or "http://localhost:11434/v1"
        url = f"{base.rstrip('/')}/models"
        headers = {}
    else:
        base = config.resolved_base_url() or "https://api.openai.com/v1"
        url = f"{base.rstrip('/')}/models"
        key = config.key_for(provider)
        headers = {"Authorization": f"Bearer {key}"} if key else {}

    # A non-Python User-Agent: Groq (and other WAF-fronted APIs) 403 the default
    # "Python-urllib" agent, which would make this check lie about a valid key.
    headers["User-Agent"] = "ronin (+https://github.com/rohithkandula19/Ronin)"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
            import json as _json
            data = _json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "auth", []
        return f"http:{e.code}", []
    except Exception as e:  # noqa: BLE001 — network / DNS / timeout / refused
        return f"unreachable:{e.__class__.__name__}", []

    ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
    return None, ids


def _tiny_completion_probe(config: RoninConfig) -> tuple[bool, Exception | None]:
    """Send a 1-token real completion through ronin's own provider layer (no
    vendor SDK). This is the true end-to-end check: it proves the configured
    *model* actually answers, catching invalid-model 404s the models list can
    miss (e.g. an alias the list endpoint doesn't surface). Returns
    ``(ok, error)``."""
    from ronin_agent_patterns import Message
    from .runner import build_single_provider

    provider = build_single_provider(config)
    # Keep retries to zero so a rate-limit/error fails fast instead of sitting in
    # the ~60s backoff — doctor must stay snappy.
    if hasattr(provider, "max_retries"):
        provider.max_retries = 0
    if hasattr(provider, "timeout"):
        provider.timeout = 12.0
    try:
        provider.complete(
            system="",
            messages=[Message(role="user", content="ping")],
            tools=[],
            max_tokens=1,
        )
        return True, None
    except Exception as e:  # noqa: BLE001 — surfaced as a remedy by the caller
        return False, e


def _provider_live_check(config: RoninConfig) -> _LiveCheck:
    """End-to-end validation of the configured provider with a crisp PASS/FAIL
    and the exact remedy on failure. Order: key present? → endpoint reachable +
    auth (models list) → does the configured model actually answer (tiny real
    completion)? Fast (short timeouts, 1-token probe) and never crashes — runs
    cleanly even with no key configured."""
    provider = config.provider
    model = config.resolved_model()
    key_env = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"

    # Step 0: key present? (ollama is local — no key needed.)
    if provider != "ollama" and not config.key_for(provider):
        return _LiveCheck(
            status="[red]no key configured[/red]",
            remedy=(f"[dim]Fix: set a key — [bold]ronin set-key[/bold] (or [bold]/login {provider}[/bold] "
                    f"in a session, or [bold]export {key_env}=...[/bold]).[/dim]"),
        )

    # Step 1: endpoint reachable + key accepted (cheap models-list probe).
    err, ids = _live_models_probe(config)
    if err == "auth":
        return _LiveCheck(
            status="[red]invalid key (401/403)[/red]",
            remedy=(f"[dim]Fix: that key was rejected — set a valid one with [bold]ronin set-key[/bold] "
                    f"(or [bold]export {key_env}=...[/bold]).[/dim]"),
        )
    if err and err.startswith("unreachable"):
        name = err.split(":", 1)[1]
        hint = ("Is the Ollama server running? Start it with [bold]ollama serve[/bold]."
                if provider == "ollama"
                else "Check your network or the base_url in your config.")
        return _LiveCheck(
            status=f"[red]endpoint unreachable[/red] [dim]({name})[/dim]",
            remedy=f"[dim]Fix: {hint}[/dim]",
        )
    # A non-auth HTTP error on the *list* endpoint isn't fatal — some providers
    # don't expose /models. Fall through to the real completion probe, which is
    # the authoritative end-to-end signal.

    # Step 1b: if we got a live id list and the configured model isn't in it,
    # that's the most common free-user 404 — point them straight at `ronin models`.
    if ids and model not in ids:
        sample = ", ".join(ids[:3])
        return _LiveCheck(
            status=f"[red]model '{model}' not served by {provider}[/red]",
            remedy=(f"[dim]Fix: run [bold]ronin models[/bold] to list valid ids, then "
                    f"[bold]ronin set-key --model <id>[/bold] (e.g. {sample}). "
                    f"Free model ids rotate often.[/dim]"),
        )

    # Step 2: the real end-to-end probe — does this model actually answer?
    ok, error = _tiny_completion_probe(config)
    if ok:
        return _LiveCheck(status="[green]ok — key + endpoint + model all valid[/green]", ok=True)

    # Map the completion failure to an actionable remedy.
    status_code = getattr(getattr(error, "response", None), "status_code", None)
    if status_code == 404 or (status_code in (400, 422) and "model" in str(error).lower()):
        return _LiveCheck(
            status=f"[red]model '{model}' invalid (HTTP {status_code})[/red]",
            remedy=("[dim]Fix: run [bold]ronin models[/bold] to see valid ids, then "
                    "[bold]ronin set-key --model <id>[/bold]. Free model ids rotate often.[/dim]"),
        )
    if status_code in (401, 403):
        return _LiveCheck(
            status=f"[red]invalid key ({status_code})[/red]",
            remedy=f"[dim]Fix: set a valid key — [bold]ronin set-key[/bold] (or export {key_env}=...).[/dim]",
        )
    if status_code == 429:
        return _LiveCheck(
            status="[red]rate-limited (429)[/red]",
            remedy=("[dim]The key + model are fine — the free tier is just throttling. Wait and retry, "
                    "or switch quota: [bold]ronin set-key --provider gemini[/bold] (or groq).[/dim]"),
        )
    name = error.__class__.__name__ if error else "error"
    return _LiveCheck(
        status=f"[red]probe failed[/red] [dim]({name})[/dim]",
        remedy=f"[dim]{str(error)[:160]}[/dim]" if error else "",
    )


@util_app.command()
def doctor(
    check: bool = typer.Option(False, "--check", help="Ping the provider to verify the key + model actually work end-to-end (network)."),
) -> None:
    """Health check: config location, provider auth, configured services.

    Add --check to live-validate the configured provider end-to-end — key
    present? endpoint reachable? does the configured model actually answer? — and
    print the exact fix on any failure (set a key, run `ronin models`, etc.)."""
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
    # Which sandbox the agent's shell runs in (RONIN_BACKEND). Surfacing it makes
    # containment observable — you can see whether commands run on the host or in a
    # backend, and that a backend failure fails closed rather than escaping to host.
    from .backends import parse_backend, requested_backend
    _bk = requested_backend()
    if _bk is None:
        table.add_row("sandbox", "[dim]host — commands run locally (set RONIN_BACKEND to contain)[/dim]")
    else:
        try:
            parse_backend(_bk)
            table.add_row("sandbox", f"[green]{_bk}[/green] "
                          "[dim](fail-closed: a backend failure refuses, never runs on host)[/dim]")
        except Exception as e:  # noqa: BLE001
            table.add_row("sandbox", f"[red]{_bk} — invalid spec: {e}[/red] "
                          "[dim](commands will be refused)[/dim]")
    # Static check only confirms a key is *present* — say so honestly. The local
    # providers (local = in-process, ollama = local server) need no key at all.
    if config.provider in ("local", "ollama"):
        table.add_row(f"{config.provider} key", "[green]not needed — runs without a key[/green]")
    else:
        table.add_row(
            f"{config.provider} key",
            "[green]present[/green]" if config.has_provider_auth() else "[red]missing[/red]",
        )
    live: _LiveCheck | None = None
    if check:
        with console.status("[dim]live-checking provider…[/dim]", spinner="dots"):
            live = _provider_live_check(config)
        table.add_row("live check", live.status)
    services = config.configured_services()
    table.add_row("services", ", ".join(services) if services else "[red]none[/red]")
    table.add_row("ronin version", display_version(__version__))

    console.print(table)
    if live is not None and live.remedy and not live.ok:
        console.print(live.remedy)
    if not check:
        console.print("[dim]tip: run [bold]ronin doctor --check[/bold] to verify the key + model actually work.[/dim]")
    # --check is a verification gate: if the live check could not confirm the
    # provider works (bad/missing key, unreachable model), exit non-zero so a
    # script/CI sees the failure. The row already states the honest reason.
    if live is not None and not live.ok:
        raise typer.Exit(1)


# ---------- saved queries ----------

@util_app.command()
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


@util_app.command()
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


@util_app.command()
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


@util_app.command(name="unsave")
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


@eval_app.command("agents", help="Run deterministic routing, workflow, and governance regression cases.")
def eval_agents() -> None:
    """Evaluate the agent control plane without provider calls or credentials."""
    from .agent_control_eval import render_agent_control_report, run_agent_control_eval

    passed = render_agent_control_report(console, run_agent_control_eval())
    if not passed:
        raise typer.Exit(1)


@eval_app.command("platform", help="Run offline queue, telemetry, semantic-memory, and sandbox regressions.")
def eval_platform() -> None:
    """Evaluate the integrated agent platform without provider calls or credentials."""
    from .agent_platform_eval import render_agent_platform_report, run_agent_platform_eval

    passed = render_agent_platform_report(console, run_agent_platform_eval())
    if not passed:
        raise typer.Exit(1)


@eval_app.command("run", help="Run a golden dataset against a target model.")
def eval_run(
    dataset: Path = typer.Argument(..., help="Path to JSONL dataset."),
    target: Optional[str] = typer.Option(
        None, "--target",
        help="Target model. Defaults to the configured provider's model (no hardcoded fallback)."),
    judge: Optional[str] = typer.Option(
        None, "--judge",
        help="LLM-judge model. Defaults to the configured provider's model."),
    criteria: str = typer.Option(
        "task_success,faithfulness,helpfulness,safety", "--criteria",
        help="Comma-separated rubric criteria.",
    ),
    label: Optional[str] = typer.Option(None, "--label"),
    json_out: str = typer.Option("eval-report.json", "--json-out"),
    out: Optional[str] = typer.Option(None, "--out", help="Optional HTML report path."),
) -> None:
    # Do NOT fall back to a hardcoded Anthropic model that 404s without a key.
    # Resolve target/judge from the configured provider, and SKIP honestly (no
    # score produced) when the model can't be resolved or there's no auth.
    config = load_config()
    if not Path(dataset).exists():
        # Graceful error instead of a raw traceback from the downstream reader.
        console.print(f"[red]✗[/red] dataset not found: [bold]{dataset}[/bold] — "
                      "pass a path to an existing JSONL dataset.")
        raise typer.Exit(2)
    resolved_target = target or config.resolved_model()
    if not resolved_target:
        console.print("[yellow]SKIPPED[/yellow]: no --target given and no model is configured. "
                      "Pass --target or run [bold]ronin init[/bold]. (Not run — no score produced.)")
        raise typer.Exit(3)
    if not (target and judge) and not config.has_provider_auth():
        console.print(f"[yellow]SKIPPED[/yellow]: no credentials for [bold]{config.provider}[/bold]. "
                      "Set a key with [bold]ronin login[/bold], or pass --target/--judge you have "
                      "access to. (Not run — no score produced.)")
        raise typer.Exit(3)
    resolved_judge = judge or resolved_target

    from ronin_eval_suite.cli import main as eval_main

    argv = ["run", str(dataset), "--target", resolved_target, "--judge", resolved_judge,
            "--criteria", criteria, "--json-out", json_out]
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


@eval_app.command("protocol", help="Run the frozen 91-task protocol eval against "
                  "an adapter (with --baseline for the base-model delta).")
def eval_protocol(
    adapter: str = typer.Option(None, "--adapter", help="Path to a trained LoRA adapter dir."),
    model: str = typer.Option("mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit", "--model"),
    baseline: bool = typer.Option(False, "--baseline",
                                  help="Also score the bare base model — every adapter "
                                  "number ships next to its baseline delta."),
    evals: str = typer.Option("training/data/evals/ronin_protocol_eval.jsonl", "--evals"),
) -> None:
    """Honest, frozen, reproducible: refuses a contaminated eval set (sha256 pin),
    stamps model+adapter+commit into a timestamped training/reports/ file."""
    try:
        from ronin_training.eval_runner import main as _eval_main
    except ImportError:
        console.print("[red]x[/red] the protocol eval needs the training workspace: "
                      "run from a repo checkout with [cyan]uv sync --all-packages[/cyan]")
        raise typer.Exit(2)
    argv = ["--model", model, "--evals", evals]
    if adapter:
        argv += ["--adapter", adapter]
    if baseline:
        argv += ["--baseline"]
    raise typer.Exit(_eval_main(argv))


@util_app.command("models")
def models_cmd() -> None:
    """List the provider paths ronin can run on — including the embedded local
    model and the ronin-code-1.5b adapter path (RONIN_ADAPTER)."""
    import os as _os

    table = Table(title="providers", box=box.ROUNDED)
    table.add_column("provider", style="cyan", no_wrap=True)
    table.add_column("default model")
    table.add_column("needs key")
    for name, preset in PROVIDER_PRESETS.items():
        needs = "no (local)" if name in ("ollama", "local") else "yes"
        table.add_row(name, preset.get("model") or "(auto by RAM)", needs)
    console.print(table)
    adapter = _os.environ.get("RONIN_ADAPTER", "")
    if adapter:
        from .embedded_provider import validate_adapter_dir
        try:
            validate_adapter_dir(adapter)
            console.print(f"[green]✓[/green] local (ronin-code-1.5b): adapter [bold]{adapter}[/bold] valid")
        except FileNotFoundError as e:
            console.print(f"[red]x[/red] RONIN_ADAPTER is set but unusable — the local "
                          f"provider will REFUSE to start (fail-closed): {e}")
    else:
        console.print("[dim]local runs the bare base model; set "
                      "RONIN_ADAPTER=training/adapters/<name>/adapters for ronin-code-1.5b "
                      "(train it with training/cuda/run.sh).[/dim]")


# ---------- plugin (scaffold custom tools) ----------

plugin_app = typer.Typer(help="Scaffold & manage custom Python tool plugins (.ronin/plugins/).")
util_app.add_typer(plugin_app, name="plugin")


@plugin_app.command("new", help="Scaffold a working plugin: ronin plugin new weather")
def plugin_new(
    name: str = typer.Argument(..., help="Tool name (lowercase, e.g. 'weather', 'github_trending')."),
    desc: str = typer.Option("", "--desc", "-d", help="One-line description of what the tool does."),
    force: bool = typer.Option(False, "--force", help="Overwrite if the plugin already exists."),
    root: Path = typer.Option(Path("."), "--root", help="Project root (writes to <root>/.ronin/plugins/)."),
) -> None:
    from .plugin_scaffold import write_plugin
    from .plugin_trust import trust as _trust
    try:
        path = write_plugin(name, root, description=desc, overwrite=force)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)
    except FileExistsError as e:
        console.print(f"[yellow]already exists:[/yellow] {e} [dim](use --force to overwrite)[/dim]")
        raise typer.Exit(1)
    _trust(path)   # the user creating it IS the consent; repo-committed files are not
    console.print(f"[green]✓[/green] scaffolded plugin [bold]{name}[/bold] → [cyan]{path}[/cyan]")
    console.print(f"  [dim]edit the [bold]{name}()[/bold] function to call your API/logic, then run "
                  "[bold]ronin[/bold] — the agent picks it up automatically.[/dim]")
    console.print("  [dim]list plugins with [bold]ronin plugins[/bold].[/dim]")


@plugin_app.command("list", help="List loaded plugins (alias of 'ronin plugins').")
def plugin_list() -> None:
    plugins()


@plugin_app.command("library", help="Browse ready-to-use plugins you can add in one command.")
def plugin_library_cmd() -> None:
    from .plugin_library import installed_names, library_rows
    have = installed_names(".")
    grid = Table(box=box.ROUNDED, show_header=True, header_style="bold #7aa2f7")
    grid.add_column("", no_wrap=True)
    grid.add_column("add", style="cyan", no_wrap=True)
    grid.add_column("needs", style="#e0af68", no_wrap=True)
    grid.add_column("what it does")
    for name, needs, blurb in library_rows():
        mark = "[green]✓[/green]" if name in have else " "
        grid.add_row(mark, name, needs, blurb)
    console.print(grid)
    console.print(f"[dim]✓ = installed ({len(have & {r[0] for r in library_rows()})}/{len(library_rows())}) · "
                  "add with [bold]ronin plugin add <name>[/bold] · "
                  "make your own with [bold]ronin plugin new <name>[/bold][/dim]")


@plugin_app.command("update", help="Refresh installed library plugins to their latest version.")
def plugin_update(
    name: str = typer.Argument(None, help="A specific plugin to update (default: all outdated)."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    from .plugin_library import LIBRARY, outdated
    pdir = Path(root) / ".ronin" / "plugins"
    targets = [name] if name else outdated(root)
    if name and name not in LIBRARY:
        console.print(f"[yellow]'{name}' isn't a library plugin[/yellow] — nothing to update.")
        raise typer.Exit(1)
    if not targets:
        console.print("[green]✓ all installed library plugins are up to date.[/green]")
        return
    from .plugin_trust import trust as _trust
    for n in targets:
        dest = pdir / f"{n}.py"
        dest.write_text(LIBRARY[n].source, encoding="utf-8")
        _trust(dest)   # rewriting from the shipped library changes the digest; re-trust it
        console.print(f"[green]✓[/green] updated [bold]{n}[/bold]")
    console.print(f"[dim]refreshed {len(targets)} plugin(s) — live next time you run ronin.[/dim]")


@plugin_app.command("from-api", help="Turn any REST endpoint into a tool: ronin plugin from-api dogpic https://dog.ceo/api/breeds/image/random --field message")
def plugin_from_api(
    name: str = typer.Argument(..., help="Tool name (lowercase), e.g. 'weather'."),
    url: str = typer.Argument(..., help="Endpoint URL; use {placeholders} for parameters, e.g. .../{city}."),
    field: Optional[list[str]] = typer.Option(None, "--field", "-f", help="JSON field to extract, dotted path (e.g. data.0.title). Repeatable; omit to return the whole response."),
    desc: str = typer.Option("", "--desc", help="One-line description for the agent."),
    method: str = typer.Option("GET", "--method", help="HTTP method."),
    force: bool = typer.Option(False, "--force", help="Overwrite if it exists."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    from .plugin_from_api import generate_api_plugin
    try:
        src = generate_api_plugin(name, url, fields=list(field or []), blurb=desc, method=method)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)
    pdir = Path(root) / ".ronin" / "plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{name}.py"
    if path.exists() and not force:
        console.print(f"[yellow]already exists:[/yellow] {path} [dim](use --force)[/dim]")
        raise typer.Exit(1)
    path.write_text(src, encoding="utf-8")
    from .plugin_trust import trust as _trust
    _trust(path)   # user-generated from a URL they supplied — that is the consent
    from .plugin_from_api import url_params
    params = url_params(url)
    console.print(f"[green]✓[/green] generated tool [bold]{name}[/bold] → [cyan]{path}[/cyan]")
    if params:
        console.print(f"  [dim]parameters:[/dim] {', '.join(params)}")
    if field:
        console.print(f"  [dim]extracts:[/dim] {', '.join(field)}")
    console.print("  [dim]live in the agent next run · edit the file to refine.[/dim]")


@plugin_app.command("search", help="Search the plugin library: ronin plugin search finance")
def plugin_search(
    query: str = typer.Argument(..., help="A keyword, e.g. 'crypto', 'word', 'github'."),
) -> None:
    from .plugin_library import search
    hits = search(query)
    if not hits:
        console.print(f"[yellow]no library plugins match[/yellow] '{query}' "
                      "[dim](try [bold]ronin plugin library[/bold] for the full list)[/dim]")
        raise typer.Exit(1)
    console.print(f"[#7aa2f7]🔎 {len(hits)} match(es) for[/#7aa2f7] [bold]{query}[/bold]")
    for p in hits:
        console.print(f"  [cyan]{p.name:<16}[/cyan] [dim]{p.blurb}[/dim]")
    console.print("[dim]install with [bold]ronin plugin add <name>[/bold].[/dim]")


@plugin_app.command("add", help="Install a ready-made plugin from the library: ronin plugin add weather")
def plugin_add(
    name: str = typer.Argument(..., help="A library name, e.g. 'weather', 'hackernews'. See 'ronin plugin library'."),
    force: bool = typer.Option(False, "--force", help="Overwrite if it already exists."),
    root: Path = typer.Option(Path("."), "--root", help="Project root (writes to <root>/.ronin/plugins/)."),
) -> None:
    from .plugin_library import resolve
    entry = resolve(name)
    if entry is None:
        console.print(f"[yellow]'{name}' isn't in the library[/yellow] — see "
                      "[bold]ronin plugin library[/bold], or scaffold one with [bold]ronin plugin new[/bold].")
        raise typer.Exit(1)
    pdir = Path(root) / ".ronin" / "plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{entry.name}.py"
    if path.exists() and not force:
        console.print(f"[yellow]already exists:[/yellow] {path} [dim](use --force)[/dim]")
        raise typer.Exit(1)
    path.write_text(entry.source, encoding="utf-8")
    from .plugin_trust import trust as _trust
    _trust(path)   # installed from the shipped library on the user's command
    console.print(f"[green]✓[/green] installed plugin [bold]{entry.name}[/bold] "
                  f"[dim]{entry.blurb}[/dim] → [cyan]{path}[/cyan]")
    console.print("  [dim]live in the agent next time you run [bold]ronin[/bold].[/dim]")


@plugin_app.command("trust", help="Trust a plugin file so ronin may import it: ronin plugin trust weather")
def plugin_trust_cmd(
    name: str = typer.Argument(None, help="Plugin name or file (default: show what is untrusted)."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
    all_: bool = typer.Option(False, "--all", help="Trust every untrusted plugin in this repo."),
) -> None:
    """🔐 Approve a plugin for import.

    `.ronin/plugins/` lives inside the repository, and importing a plugin EXECUTES
    it — before any tool call, so the approval gate never sees it. ronin therefore
    refuses to import a plugin file it has not been told to trust. Read the file
    first; trusting it is equivalent to running it.
    """
    from .plugin_trust import trust as _trust
    from .plugin_trust import untrusted_in

    pdir = Path(root) / ".ronin" / "plugins"
    pending = untrusted_in(pdir)

    if not name and not all_:
        if not pending:
            console.print("[#9ece6a]✓ every plugin in this repo is trusted.[/#9ece6a]")
            return
        console.print(f"[#e0af68]{len(pending)} untrusted plugin(s) — not imported:[/#e0af68]")
        for f in pending:
            console.print(f"  {f}")
        console.print("[dim]review the file, then: ronin plugin trust <name> (or --all)[/dim]")
        return

    targets = pending if all_ else [pdir / (name if name.endswith(".py") else f"{name}.py")]
    n = 0
    for f in targets:
        if not f.is_file():
            console.print(f"[#f7768e]no such plugin: {f}[/#f7768e]")
            raise typer.Exit(2)
        _trust(f)
        console.print(f"[#9ece6a]✓[/#9ece6a] trusted {f.name}")
        n += 1
    if n:
        console.print("[dim]it will be imported next time you run ronin.[/dim]")


@plugin_app.command("untrust", help="Revoke trust so ronin stops importing a plugin.")
def plugin_untrust_cmd(
    name: str = typer.Argument(..., help="Plugin name or file."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """🔒 Revoke a plugin's trust — ronin will refuse to import it again."""
    from .plugin_trust import untrust as _untrust

    pdir = Path(root) / ".ronin" / "plugins"
    f = pdir / (name if name.endswith(".py") else f"{name}.py")
    if _untrust(f):
        console.print(f"[#9ece6a]✓[/#9ece6a] revoked trust for {f.name} — it will not be imported.")
    else:
        console.print(f"[dim]{f.name} was not trusted[/dim]")


@plugin_app.command("remove", help="Delete a plugin file: ronin plugin remove weather")
def plugin_remove(
    name: str = typer.Argument(..., help="The plugin name (file stem) to remove."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    path = Path(root) / ".ronin" / "plugins" / f"{name}.py"
    if not path.is_file():
        console.print(f"[yellow]no plugin '{name}'[/yellow] at [dim]{path}[/dim] "
                      "[dim](see [bold]ronin plugins[/bold])[/dim]")
        raise typer.Exit(1)
    path.unlink()
    console.print(f"[green]✓[/green] removed plugin [bold]{name}[/bold] [dim]({path})[/dim]")


# ---------- mcp ----------

mcp_app = typer.Typer(help="Connect MCP servers (Anthropic's tool protocol) — their tools join the agent.")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve", help="Run ronin ITSELF as a stdio MCP server — exposes read-only ronin "
                               "tools (ask / consensus / research) so any MCP client can call ronin.")
def mcp_serve() -> None:
    from .mcp_server import serve
    serve()


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


@mcp_app.command("trust", help="Trust .ronin/mcp.json so its servers may start (they run at launch).")
def mcp_trust_cmd(
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """🔐 Approve this repo's MCP config.

    Each server's command is executed at startup — before any tool call — so a
    repo-committed mcp.json is arbitrary code execution and, via ``passEnv``, secret
    exfiltration. ronin refuses to start servers from a config you have not trusted.
    Trusting it is equivalent to agreeing to run the commands shown below; review
    them first.
    """
    from .mcp_client import load_mcp_servers, mcp_config_path
    from .plugin_trust import is_trusted, trust

    cfg = mcp_config_path(root)
    if not cfg.is_file():
        console.print("[dim]no .ronin/mcp.json here[/dim]")
        raise typer.Exit(1)
    if is_trusted(cfg):
        console.print("[#9ece6a]✓ already trusted.[/#9ece6a]")
        return

    servers = load_mcp_servers(root)
    console.print(f"[bold]this will let {len(servers)} server(s) run at launch:[/bold]")
    for name, spec in servers.items():
        if spec.get("url"):
            console.print(f"  [cyan]{name}[/cyan]  → {spec['url']} [dim](remote)[/dim]")
        else:
            passenv = spec.get("passEnv") or []
            secret = f"  [#f7768e](inherits: {', '.join(passenv)})[/#f7768e]" if passenv else ""
            console.print(f"  [cyan]{name}[/cyan]  {spec.get('command','')} "
                          f"{' '.join(spec.get('args', []))}{secret}")
    trust(cfg)
    console.print("[#9ece6a]✓[/#9ece6a] trusted — servers load next time you run ronin.")


@mcp_app.command("untrust", help="Revoke trust so .ronin/mcp.json servers stop starting.")
def mcp_untrust_cmd(
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """🔒 Revoke this repo's MCP config — its servers will not start again."""
    from .mcp_client import mcp_config_path
    from .plugin_trust import untrust

    cfg = mcp_config_path(root)
    if untrust(cfg):
        console.print("[#9ece6a]✓[/#9ece6a] revoked — mcp.json servers will not start.")
    else:
        console.print("[dim].ronin/mcp.json was not trusted[/dim]")


@mcp_app.command("add", help="Add an MCP server: ronin mcp add NAME COMMAND [ARGS...]",
                 context_settings={"ignore_unknown_options": True})
def mcp_add(
    name: str = typer.Argument(..., help="A short name, e.g. 'fs'."),
    command: str = typer.Argument(..., help="The server command, e.g. 'npx'."),
    args: Optional[list[str]] = typer.Argument(None, help="Args for the server command (flags like -y are passed through)."),
    env: Optional[list[str]] = typer.Option(None, "--env", "-e", help="Env var for the server, KEY=VALUE (repeatable). Bare KEY inherits from your shell — good for secrets like tokens."),
) -> None:
    from .mcp_client import add_mcp_server, split_env_spec
    env_map, pass_env = split_env_spec(list(env or []))
    path = add_mcp_server(name, command, list(args or []), ".",
                          env=env_map or None, pass_env=pass_env or None)
    console.print(f"[green]✓[/green] added MCP server [bold]{name}[/bold] → [cyan]{path}[/cyan]")
    if env_map or pass_env:
        parts = [f"{k}=•••" for k in env_map] + [f"{k}=(from shell)" for k in pass_env]
        console.print(f"[dim]env: {', '.join(parts)}[/dim]")
    console.print("[dim]its tools load automatically next time you run [bold]ronin[/bold]. "
                  "Verify now with [bold]ronin mcp list[/bold].[/dim]")


@mcp_app.command("catalog", help="Browse popular MCP servers you can install in one command.")
def mcp_catalog() -> None:
    from rich.table import Table as _Table

    from .mcp_catalog import catalog_rows
    grid = _Table(box=box.ROUNDED, show_header=True, header_style="bold #7aa2f7")
    grid.add_column("install", style="cyan", no_wrap=True)
    grid.add_column("needs", style="#e0af68", no_wrap=True)
    grid.add_column("what it adds")
    for key, env, blurb in catalog_rows():
        grid.add_row(key, env, blurb)
    console.print(grid)
    console.print("[dim]add one with [bold]ronin mcp install <name>[/bold] · "
                  "any other server with [bold]ronin mcp add NAME COMMAND …[/bold][/dim]")


@mcp_app.command("install", help="Install a popular MCP server by name: ronin mcp install github")
def mcp_install(
    name: str = typer.Argument(..., help="A catalog name, e.g. 'github', 'gmail', 'slack'. See 'ronin mcp catalog'."),
) -> None:
    import os

    from .mcp_catalog import resolve
    from .mcp_client import add_mcp_server
    server = resolve(name)
    if server is None:
        console.print(f"[yellow]'{name}' isn't in the catalog[/yellow] — see "
                      "[bold]ronin mcp catalog[/bold], or use [bold]ronin mcp add[/bold] for any server.")
        raise typer.Exit(1)
    # The server declares which env vars it needs (server.env). Record them as
    # passEnv NAMES — the server inherits exactly those from the parent at spawn,
    # and nothing else. A key exported AFTER install is still picked up (resolved
    # at spawn), and an unset one is simply omitted. The server is no longer
    # launched with the whole environment, so unrelated secrets never reach it.
    missing = [k for k in server.env if not os.environ.get(k)]
    path = add_mcp_server(server.key, server.command, list(server.args), ".",
                          pass_env=list(server.env) or None)
    console.print(f"[green]✓[/green] installed [bold]{server.key}[/bold] [dim]{server.blurb}[/dim] "
                  f"→ [cyan]{path}[/cyan]")
    if server.note:
        console.print(f"  [#e0af68]note:[/#e0af68] {server.note}")
    if missing:
        console.print(f"  [#f7768e]set these before running ronin:[/#f7768e] {', '.join(missing)}")
        console.print(f"  [dim]e.g.  export {missing[0]}=…   (then they're picked up automatically)[/dim]")
    console.print("[dim]its tools load next time you run [bold]ronin[/bold] · verify with "
                  "[bold]ronin mcp list[/bold].[/dim]")


@mcp_app.command("add-remote", help="Add a hosted MCP server by URL: ronin mcp add-remote NAME https://…/mcp")
def mcp_add_remote(
    name: str = typer.Argument(..., help="A short name, e.g. 'linear'."),
    url: str = typer.Argument(..., help="The server's HTTP endpoint, e.g. https://mcp.example.com/mcp"),
    header: Optional[list[str]] = typer.Option(None, "--header", "-H", help="Auth header, 'Name: value' (repeatable). Bare 'Authorization' inherits a Bearer token from $MCP_TOKEN."),
) -> None:
    import os

    from .mcp_client import add_remote_mcp_server
    headers: dict[str, str] = {}
    for item in header or []:
        if ":" in item:
            k, v = item.split(":", 1)
            headers[k.strip()] = v.strip()
        elif item.strip().lower() == "authorization" and os.environ.get("MCP_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['MCP_TOKEN']}"
    path = add_remote_mcp_server(name, url, ".", headers=headers or None)
    console.print(f"[green]✓[/green] added remote MCP server [bold]{name}[/bold] "
                  f"[dim]{url}[/dim] → [cyan]{path}[/cyan]")
    if headers:
        console.print(f"[dim]headers: {', '.join(headers)}[/dim]")
    console.print("[dim]connects over HTTP next time you run [bold]ronin[/bold] · verify with "
                  "[bold]ronin mcp list[/bold].[/dim]")


# ---------- plugins ----------

@util_app.command()
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

@util_app.command()
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

@util_app.command()
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

@util_app.command()
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

@util_app.command()
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

@dev_app.command(name="map")
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


# ---------- pr (open a PR with AI-drafted title/body) ----------

@dev_app.command()
def pr(
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🔀 Push the current branch and open a GitHub PR with an AI-drafted title +
    body (from your commits). Gated — shows what it'll do and waits for y/N.
    """
    config = load_config()
    from .git_helper import open_pr
    open_pr(console, root, config)


# ---------- undo-commit (explain + soft-reset / revert the last commit) ----------

@dev_app.command(name="undo-commit")
def undo_commit(
    revert: bool = typer.Option(
        False, "--revert",
        help="Record a new commit that undoes the last one (instead of soft-reset)."),
    force: bool = typer.Option(
        False, "--force",
        help="Allow soft-reset even when HEAD is pushed (rewrites shared history)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """Show the last commit, then undo it — soft-reset (keeps changes staged,
    default) or a true revert. Gated: it prints the commit and waits for y/N, and
    refuses to soft-reset a pushed HEAD without --force (don't rewrite shared
    history). --revert is always safe (it adds a new commit).
    """
    from .git_helper import _git
    from .git_undo import SHOW_FORMAT, format_commit_info, is_pushed, parse_commit_info

    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        console.print("[yellow]not a git repository[/yellow]")
        raise typer.Exit(2)
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        console.print("[yellow]no commits yet — nothing to undo[/yellow]")
        raise typer.Exit(2)
    head_sha = head.stdout.strip()
    if _git(root, "rev-parse", "HEAD~1").returncode != 0:
        console.print("[yellow]this is the first commit — a soft-reset has no parent. "
                      "Use [bold]--revert[/bold] to undo it as a new commit, or reset manually.[/yellow]")
        if not revert:
            raise typer.Exit(2)

    info = parse_commit_info(_git(root, "show", "--stat", f"--format={SHOW_FORMAT}", "HEAD").stdout)
    if info is None:
        console.print("[red]couldn't read the last commit.[/red]")
        raise typer.Exit(1)

    from rich.panel import Panel
    console.print(Panel(format_commit_info(info), title="last commit", border_style="#6b7089", padding=(1, 2)))

    # Determine whether HEAD is pushed (so a rewrite would touch shared history).
    upstream = _git(root, "rev-parse", "@{upstream}")
    upstream_sha = upstream.stdout.strip() if upstream.returncode == 0 else ""
    head_in_upstream = (
        upstream_sha != ""
        and _git(root, "merge-base", "--is-ancestor", "HEAD", "@{upstream}").returncode == 0
    )
    pushed = is_pushed(upstream_sha, head_sha, head_in_upstream)

    if revert:
        action = "revert (add a new commit that undoes it)"
    else:
        action = "soft-reset HEAD~1 (keep your changes staged)"
        if pushed and not force:
            console.print(
                "[yellow]HEAD has been pushed — soft-reset would rewrite shared history.[/yellow]\n"
                "[dim]Use [bold]--revert[/bold] (safe, adds a new commit) or "
                "[bold]--force[/bold] to override.[/dim]")
            raise typer.Exit(2)
        if pushed:
            console.print("[red]warning: HEAD is pushed — --force will rewrite shared history.[/red]")

    console.print(f"[bold]action:[/bold] [cyan]{action}[/cyan]")
    if not yes and not Confirm.ask("proceed?", default=False):
        console.print("[dim]aborted[/dim]")
        raise typer.Exit(0)

    if revert:
        r = _git(root, "revert", "--no-edit", "HEAD")
        ok_msg = "[green]✓ reverted[/green] — recorded a new commit undoing it."
    else:
        r = _git(root, "reset", "--soft", "HEAD~1")
        ok_msg = "[green]✓ undone[/green] — commit removed; your changes are staged."
    if r.returncode == 0:
        console.print(ok_msg)
    else:
        console.print(f"[red]undo failed:[/red] {(r.stderr or r.stdout).strip()[:300]}")
        raise typer.Exit(1)


# ---------- stash (AI-labelled git stash) ----------

stash_app = typer.Typer(
    help="Safe AI-labelled git stash: bare 'ronin stash' saves with an auto-summary; "
         "'list' shows entries, 'pop [n]' restores.",
    invoke_without_command=True,
)
util_app.add_typer(stash_app, name="stash")


def _stash_drafter(config: RoninConfig):
    """Return a drafter callable for git_stash.summarize_diff, or None when offline.

    The callable wraps git_helper._draft (the configured provider). When there is no
    provider auth we return None so summarize_diff uses its deterministic fallback.
    """
    if not config.has_provider_auth():
        return None
    from .git_helper import _draft

    def _draft_label(system: str, prompt: str) -> str:
        return _draft(config, system, prompt, max_tokens=80)

    return _draft_label


@stash_app.callback(invoke_without_command=True)
def stash_default(
    ctx: typer.Context,
    no_ai: bool = typer.Option(False, "--no-ai", help="Use the offline heuristic label instead of the model."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """Bare ``ronin stash`` → save the working tree with an AI-summarized label."""
    if ctx.invoked_subcommand is not None:
        return  # a subcommand (list / pop) was given
    from .git_helper import _git
    from .git_stash import summarize_diff

    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        console.print("[yellow]not a git repository[/yellow]")
        raise typer.Exit(2)
    # Include untracked files in the diff so the summary reflects the full change set.
    diff = _git(root, "diff", "HEAD").stdout
    names = [n for n in _git(root, "diff", "HEAD", "--name-only").stdout.split() if n]
    untracked = [n for n in _git(root, "ls-files", "--others", "--exclude-standard").stdout.split() if n]
    names = names + untracked
    if not _git(root, "status", "--porcelain").stdout.strip():
        console.print("[dim]nothing to stash — working tree clean[/dim]")
        return
    diff_stat = _git(root, "diff", "HEAD", "--stat").stdout

    config = load_config()
    drafter = None if no_ai else _stash_drafter(config)
    with console.status("[dim] summarizing changes…[/dim]", spinner="dots"):
        label = summarize_diff(diff, diff_stat, names, drafter=drafter)

    # -u stashes untracked files too, so the summary and the stash agree.
    r = _git(root, "stash", "push", "-u", "-m", label)
    if r.returncode == 0:
        console.print(f"[green]✓ stashed[/green] [cyan]{label}[/cyan]")
        console.print("[dim]restore with [bold]ronin stash pop[/bold][/dim]")
    else:
        console.print(f"[red]stash failed:[/red] {(r.stderr or r.stdout).strip()[:200]}")
        raise typer.Exit(1)


@stash_app.command("list")
def stash_list(
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """List stash entries with their summaries."""
    from .git_helper import _git
    from .git_stash import format_stash_list, parse_stash_list

    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        console.print("[yellow]not a git repository[/yellow]")
        raise typer.Exit(2)
    entries = parse_stash_list(_git(root, "stash", "list").stdout)
    if not entries:
        console.print("[dim]no stashes.[/dim]")
        return
    console.print(format_stash_list(entries))


@stash_app.command("pop")
def stash_pop(
    n: int = typer.Argument(0, help="Stash index to restore (default: 0, the most recent)."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """Restore (pop) a stash by index, default the most recent."""
    from .git_helper import _git

    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        console.print("[yellow]not a git repository[/yellow]")
        raise typer.Exit(2)
    r = _git(root, "stash", "pop", f"stash@{{{n}}}")
    if r.returncode == 0:
        console.print(f"[green]✓ restored[/green] stash@{{{n}}}")
        out = (r.stdout or "").strip()
        if out:
            console.print(f"[dim]{out[:300]}[/dim]")
    else:
        console.print(f"[red]pop failed:[/red] {(r.stderr or r.stdout).strip()[:200]}")
        raise typer.Exit(1)


# ---------- style (fine-tuning dataset from your sessions) ----------

@util_app.command()
def style(
    out: str = typer.Option("ronin-style.jsonl", "--out", help="Output JSONL path."),
    with_bushido: bool = typer.Option(True, "--bushido/--no-bushido", help="Prepend your code-of-honor as the system message."),
    here: bool = typer.Option(False, "--here", help="Only this repo's sessions (default: all)."),
    root: Path = typer.Option(Path("."), "--root", help="Repo (with --here)."),
) -> None:
    """🧬 Export your past sessions as a fine-tuning dataset (chat JSONL) — the first
    step toward a local model that codes in *your* style, free.

    Pairs each request with ronin's response; --bushido prepends your standing
    conventions as the system message. Train a local/hosted model on the result.
    """
    from .sessions import list_sessions, load_session
    from .style import build_dataset, to_jsonl

    metas = list_sessions(root if here else None)
    transcripts = [load_session(str(m.get("id"))) for m in metas]
    system = None
    if with_bushido:
        from .bushido import load_bushido
        system = load_bushido()
    rows = build_dataset(transcripts, system=system)
    if not rows:
        console.print("[dim]no usable session data yet — code with ronin a while, then re-run.[/dim]")
        return
    try:
        Path(out).write_text(to_jsonl(rows), encoding="utf-8")
    except OSError as e:
        console.print(f"[red]couldn't write {out}: {e}[/red]")
        raise typer.Exit(1)
    sysline = " (with Bushido system prompt)" if system else ""
    console.print(f"[green]✓ {len(rows)} training example(s){sysline} →[/green] [cyan]{out}[/cyan]\n"
                  "[dim]fine-tune a local model on this to make ronin code in your style, free.[/dim]")


# ---------- patches (review/apply nightshift output) ----------

@dev_app.command()
def patches(
    apply: bool = typer.Option(False, "--apply", help="Apply the patches to your working tree."),
    pr: bool = typer.Option(False, "--pr", help="Open a GitHub PR per patch (branch + push + gh)."),
    clean: bool = typer.Option(False, "--clean", help="Only patches with no Duel blockers."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """📦 Review, apply, or PR the patches from your last `ronin nightshift --execute`.

    Lists each task + status + blockers. `--apply` applies the ready patches to
    your working tree; `--pr` opens a GitHub PR per patch (each on its own branch,
    built in an isolated worktree so your checkout is untouched). `--clean` skips
    any a Duel flagged.
    """
    from .nightshift import apply_patch, applicable, load_report

    report = load_report(root)
    if not report:
        console.print("[dim]no nightshift report yet — run [bold]ronin nightshift --execute[/bold].[/dim]")
        return

    table = Table(title="🌙 last nightshift", box=box.ROUNDED)
    table.add_column("status")
    table.add_column("task", style="bold")
    table.add_column("notes", style="#6b7089")
    _style = {"patched": "[green]patched[/green]", "failed-tests": "[red]failed[/red]",
              "no-change": "[dim]no-change[/dim]", "error": "[red]error[/red]",
              "skipped-budget": "[yellow]skipped[/yellow]"}
    for r in report:
        note = r.get("note", "")
        if r.get("blockers"):
            note = f"⚠ {len(r['blockers'])} blocker(s) · {note}"
        table.add_row(_style.get(r.get("status"), r.get("status", "?")), r.get("title", "")[:50], note[:40])
    console.print(table)

    if not apply and not pr:
        ready = applicable(report)
        if ready:
            console.print(f"[dim]{len(ready)} patch(es) ready — [bold]ronin patches --apply[/bold] "
                          "or [bold]--pr[/bold] (add --clean to skip flagged ones).[/dim]")
        return

    if pr:
        from .nightshift_pr import branch_commit_in_worktree, branch_name, open_pr, push_branch
        ready = applicable(report, clean_only=clean)
        if not ready:
            console.print("[dim]nothing to open.[/dim]")
            return
        opened = 0
        for i, r in enumerate(ready, 1):
            br = branch_name(r["title"], i)
            ok, note = branch_commit_in_worktree(root, r["patch_path"], br,
                                                 f"nightshift: {r['title']}")
            if not ok:
                console.print(f"  [red]✗[/red] {r['title'][:45]} [dim]({note})[/dim]")
                continue
            if not push_branch(root, br):
                console.print(f"  [yellow]⊘[/yellow] {r['title'][:45]} [dim](committed {br}, push failed)[/dim]")
                continue
            url = open_pr(root, br, f"nightshift: {r['title']}",
                          f"Autonomous patch from `ronin nightshift`.\n\n{r.get('note','')}")
            opened += 1 if url else 0
            console.print(f"  [green]✓[/green] {r['title'][:45]} [dim]→ {url or br}[/dim]")
        console.print(f"[green]opened {opened} PR(s)[/green]")
        return

    to_apply = applicable(report, clean_only=clean)
    if not to_apply:
        console.print("[dim]nothing to apply.[/dim]")
        return
    ok = 0
    for r in to_apply:
        applied = apply_patch(root, r["patch_path"])
        ok += 1 if applied else 0
        mark = "[green]✓[/green]" if applied else "[red]✗ (conflict)[/red]"
        console.print(f"  {mark} {r['title'][:50]}")
    console.print(f"[green]applied {ok}/{len(to_apply)} patch(es)[/green] "
                  "[dim]— review the changes, then commit.[/dim]")


# ---------- swarm (multi-provider agent team) ----------

@util_app.command()
def swarm(
    task: str = typer.Argument(..., help="The task for the team to work."),
    roster: str = typer.Option(
        None, "--roster", "-r",
        help="Role→provider, e.g. 'architect=anthropic,implementer=cerebras:gpt-oss-120b,reviewer=gemini'."),
    yolo: bool = typer.Option(False, "--yolo", help="Auto-approve edits (trusted sandboxes)."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """🐝 A TEAM of role-specialized agents works one task — an architect plans, an
    implementer codes, a reviewer critiques, the implementer revises. Each role can
    run on a DIFFERENT vendor's model (a dream team only a provider-agnostic agent
    can assemble).

    Example:  ronin swarm "add retry to the http client" \\
              -r architect=anthropic,implementer=cerebras,reviewer=gemini
    """
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .swarm import parse_roster, role_label, run_swarm

    ra = parse_roster(roster)
    line = " · ".join(f"{r}: [bold]{role_label(config, ra, r)}[/bold]" for r in ("architect", "implementer", "reviewer"))
    console.print(f"[#7aa2f7]🐝 swarm[/#7aa2f7] {line}\n")
    result = run_swarm(config, task, root=root, console=console, roster=ra, yolo=yolo)
    console.print()
    if result.blocked:
        console.print(f"[red]✗[/red] {result.output}")
        raise typer.Exit(2)
    console.print("[bold green]✅ swarm done[/bold green]")


# ---------- orchestrate (plan -> provider-agnostic sub-agents -> synthesize) ----------

@util_app.command("agents")
def agents(
    task: Optional[str] = typer.Argument(None, help="Optional task to rank specialist profiles for."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    max_agents: int = typer.Option(8, "--max-agents", help="Maximum task-relevant profiles to show."),
    agent_manifest: Optional[Path] = typer.Option(
        None, "--agent-manifest", help="Project profile manifest (default: .ronin/agents.json).",
    ),
) -> None:
    """Show Ronin's scalable specialist catalog or the team selected for a task."""
    from .orchestrate import agent_catalog, select_agents_with_context

    try:
        catalog = agent_catalog(root, manifest=agent_manifest)
        selection = select_agents_with_context(
            task or "", root, max_agents=max_agents, manifest=agent_manifest,
        )
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    project_count = sum(profile.source == "project" for profile in catalog)
    console.print(
        f"[#7aa2f7]agents[/#7aa2f7] [bold]{len(catalog):,}[/bold] available "
        f"[dim]({project_count} project-defined; {len(selection.profiles)} selected)[/dim]"
    )
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Profile", no_wrap=True)
    table.add_column("Tier")
    table.add_column("Why")
    table.add_column("Description")
    for profile in selection.profiles:
        table.add_row(
            profile.key,
            profile.tier,
            ", ".join(selection.reasons.get(profile.key, ("selected",))),
            profile.description,
        )
    console.print(table)


mission_app = typer.Typer(
    help="Create and inspect durable issue-to-PR missions and candidate workspaces.",
    no_args_is_help=True,
)
util_app.add_typer(mission_app, name="mission")

mission_workspace_app = typer.Typer(
    help="Create, inspect, verify, and destroy disposable mission candidate workspaces.",
    no_args_is_help=True,
)
mission_app.add_typer(mission_workspace_app, name="workspace")

mission_events_app = typer.Typer(
    help="Inspect and replay the durable schema-first mission event bus.",
    no_args_is_help=True,
)
mission_app.add_typer(mission_events_app, name="events")

mission_worker_app = typer.Typer(
    help="Dispatch bounded candidate verification jobs to authenticated remote Docker workers.",
    no_args_is_help=True,
)
mission_app.add_typer(mission_worker_app, name="worker")


@mission_app.command("create")
def mission_create(
    title: str = typer.Argument(..., help="Short mission title."),
    issue_text: str = typer.Argument(..., help="Issue body or operator-provided work request."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    source: str = typer.Option("manual", "--source", help="manual, github, or gitlab."),
    source_id: str = typer.Option("", "--source-id", help="Optional issue identifier from the source."),
    repository_ref: str = typer.Option("", "--repository-ref", help="Optional issue repository reference."),
    requirement: List[str] = typer.Option([], "--requirement", help="Repeatable explicit requirement."),
    acceptance: List[str] = typer.Option([], "--acceptance", help="Repeatable acceptance criterion."),
    max_tokens: int = typer.Option(100_000, "--max-tokens", min=1, max=10_000_000),
    max_cost_usd: float = typer.Option(25.0, "--max-cost-usd", min=0.0, max=1_000_000.0),
    max_wall_time: int = typer.Option(1_800, "--max-wall-time", min=1, max=604_800),
    max_tool_calls: int = typer.Option(500, "--max-tool-calls", min=1, max=100_000),
    max_concurrency: int = typer.Option(4, "--max-concurrency", min=1, max=32),
    max_repairs: int = typer.Option(3, "--max-repairs", min=0, max=10),
) -> None:
    """Record structured issue intent and immutable mission audit evidence only."""
    from .mission_models import MissionBudget, MissionSpec
    from .mission_store import MissionStore

    try:
        spec = MissionSpec(
            source=source.strip().lower(),
            source_id=source_id.strip(),
            title=title,
            issue_text=issue_text,
            repository_ref=repository_ref.strip(),
            requirements=requirement,
            acceptance_criteria=acceptance,
        )
        budget = MissionBudget(
            max_tokens=max_tokens,
            max_cost_usd=max_cost_usd,
            max_wall_time_seconds=max_wall_time,
            max_tool_calls=max_tool_calls,
            max_concurrency=max_concurrency,
            max_repair_attempts=max_repairs,
        )
        mission = MissionStore(root).create(spec, budget=budget)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(
        f"[green]created[/green] [cyan]{mission.id}[/cyan] "
        "[dim](pending inspection; no provider, workspace, command, or write was started)[/dim]"
    )


@mission_app.command("import")
def mission_import(
    source: str = typer.Argument(..., help="Issue source: github or gitlab."),
    reference: str = typer.Argument(..., help="GitHub owner/repository#number or GitLab group/project#number."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    gitlab_url: str = typer.Option("https://gitlab.com", "--gitlab-url", help="HTTPS GitLab base URL for self-hosted instances."),
    max_tokens: int = typer.Option(100_000, "--max-tokens", min=1, max=10_000_000),
    max_cost_usd: float = typer.Option(25.0, "--max-cost-usd", min=0.0, max=1_000_000.0),
    max_wall_time: int = typer.Option(1_800, "--max-wall-time", min=1, max=604_800),
    max_tool_calls: int = typer.Option(500, "--max-tool-calls", min=1, max=100_000),
    max_concurrency: int = typer.Option(4, "--max-concurrency", min=1, max=32),
    max_repairs: int = typer.Option(3, "--max-repairs", min=0, max=10),
) -> None:
    """Import one remote issue as an attributed MissionSpec; no agent starts.

    GitHub uses the authenticated ``gh`` CLI. GitLab requires
    ``GITLAB_TOKEN`` or ``GITLAB_PERSONAL_ACCESS_TOKEN`` and never prints it.
    """
    from .mission_models import MissionBudget
    from .mission_sources import IssueImportError, MissionIssueImporter

    budget = MissionBudget(
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
        max_wall_time_seconds=max_wall_time,
        max_tool_calls=max_tool_calls,
        max_concurrency=max_concurrency,
        max_repair_attempts=max_repairs,
    )
    try:
        mission = MissionIssueImporter(root, gitlab_url=gitlab_url).import_issue(
            source, reference, budget=budget,
        )
    except (IssueImportError, ValueError) as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(
        f"[green]imported[/green] [cyan]{mission.id}[/cyan] from "
        f"[bold]{mission.spec.source}:{mission.spec.source_id}[/bold] "
        "[dim](source context saved; no agent, command, or write was started)[/dim]"
    )


@mission_app.command("list")
def mission_list(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Maximum recent missions to show."),
) -> None:
    """List locally durable missions and their present review stage."""
    from .mission_store import MissionStore

    missions = MissionStore(root).list(limit=limit)
    if not missions:
        console.print("[dim]no missions saved for this project.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Mission", no_wrap=True)
    table.add_column("Stage")
    table.add_column("Candidate")
    table.add_column("Events", justify="right")
    table.add_column("Title", overflow="fold")
    for mission in missions:
        table.add_row(
            mission.id,
            mission.stage.value,
            mission.candidate_workspace_id or "-",
            str(mission.event_count),
            mission.spec.title,
        )
    console.print(table)


@mission_app.command("show")
def mission_show(
    mission_id: str = typer.Argument(..., help="Saved mission id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Show structured intent, typed evidence, and the current state-machine stage."""
    from .mission_store import MissionStore

    try:
        mission = MissionStore(root).load(mission_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    _print_mission(mission)


@mission_app.command("advance")
def mission_advance(
    mission_id: str = typer.Argument(..., help="Saved mission id."),
    stage: str = typer.Argument(..., help="Target state-machine stage."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    summary: str = typer.Option("", "--summary", help="Concise operator evidence for this transition."),
) -> None:
    """Record one valid state transition; this command never executes an agent."""
    from .mission_models import MissionStage
    from .mission_store import MissionStore

    try:
        target = MissionStage(stage.strip().lower())
        mission = MissionStore(root).transition(mission_id, target, summary=summary)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]advanced[/green] [cyan]{mission.id}[/cyan] to [bold]{mission.stage.value}[/bold]")


@mission_app.command("audit")
def mission_audit(
    mission_id: str = typer.Argument(..., help="Saved mission id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Verify the local append-only mission audit chain against its snapshot."""
    from .mission_store import MissionStore

    try:
        report = MissionStore(root).verify_audit(mission_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    if not report.valid:
        console.print(f"[red]invalid[/red] mission audit: {report.error or 'unknown error'}")
        raise typer.Exit(1)
    console.print(f"[green]valid[/green] mission audit ({report.events} event(s))")


@mission_events_app.command("list")
def mission_events_list(
    mission_id: Optional[str] = typer.Option(None, "--mission", help="Limit events to one mission id."),
    topic: Optional[str] = typer.Option(None, "--topic", help="Limit events to a typed topic."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    limit: int = typer.Option(50, "--limit", min=1, max=500, help="Maximum recent events."),
) -> None:
    """List safe mission event envelopes; issue text and agent output stay private."""
    from .mission_events import MissionEventBus

    try:
        events = MissionEventBus(root).events(mission_id=mission_id, topic=topic, limit=limit)
    except (ValueError, TypeError) as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    if not events:
        console.print("[dim]no durable mission bus events saved for this project.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Seq", justify="right")
    table.add_column("Topic")
    table.add_column("Mission", no_wrap=True)
    table.add_column("Producer")
    table.add_column("Transition")
    for event in events:
        transition = " -> ".join(part for part in (event.payload.from_stage, event.payload.to_stage) if part) or "-"
        table.add_row(str(event.sequence), event.topic, event.mission_id, event.producer, transition)
    console.print(table)


@mission_events_app.command("verify")
def mission_events_verify(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Verify the append-only mission event bus chain and idempotency keys."""
    from .mission_events import MissionEventBus

    report = MissionEventBus(root).verify()
    if not report.valid:
        console.print(f"[red]invalid[/red] mission event bus: {report.error or 'unknown error'}")
        raise typer.Exit(1)
    console.print(f"[green]valid[/green] mission event bus ({report.events} event(s))")


@mission_events_app.command("replay")
def mission_events_replay(
    mission_id: str = typer.Argument(..., help="Mission whose immutable audit events should be delivered."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Idempotently backfill the bus from a mission's locally verified audit log."""
    from .mission_store import MissionStore

    try:
        count = MissionStore(root).replay_event_bus(mission_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]replayed[/green] {count} audit event(s) into the mission bus")


@mission_app.command("plan")
def mission_plan(
    mission_id: str = typer.Argument(..., help="Saved mission id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    step: List[str] = typer.Option([], "--step", help="Repeatable implementation step."),
    file: List[str] = typer.Option([], "--file", help="Repeatable repository-relative file expected to change."),
    test_addition: List[str] = typer.Option([], "--test-addition", help="Repeatable planned regression test."),
    rollback: str = typer.Option("", "--rollback", help="Rollback strategy for this mission."),
) -> None:
    """Record a typed plan and advance the mission to candidate implementation."""
    if not step:
        console.print("[red]x[/red] provide at least one --step; plans cannot be empty")
        raise typer.Exit(2)
    from .mission_models import PlanArtifact, PlanStep
    from .mission_workflow import MissionWorkflow

    plan = PlanArtifact(
        steps=[PlanStep(id=f"step-{index}", description=description, files=file) for index, description in enumerate(step, start=1)],
        files_to_change=file,
        test_additions=test_addition,
        rollback_strategy=rollback,
    )
    try:
        mission = MissionWorkflow(root).record_plan(mission_id, plan)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]plan recorded[/green] [cyan]{mission.id}[/cyan] is now [bold]{mission.stage.value}[/bold]")


@mission_app.command("verify")
def mission_verify(
    mission_id: str = typer.Argument(..., help="Mission with an active candidate checkout."),
    command: str = typer.Argument(..., help="Verification command to run in the candidate Docker container."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    timeout: int = typer.Option(600, "--timeout", min=1, max=3_600, help="Verification deadline in seconds."),
    yes: bool = typer.Option(False, "--yes", help="Confirm candidate verification."),
) -> None:
    """Run the mission test gate in Docker and route only its actual result."""
    if not yes:
        console.print("[yellow]not verified[/yellow] (review the candidate and rerun with --yes)")
        raise typer.Exit(2)
    from .mission_models import MissionStage
    from .mission_workflow import MissionWorkflow

    try:
        mission = MissionWorkflow(root).verify_candidate(mission_id, command, timeout=timeout)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    report = mission.artifacts.test_report
    verdict = report.verdict if report else "unknown"
    console.print(
        f"[green]verification recorded[/green] [cyan]{mission.id}[/cyan] "
        f"[dim]verdict={verdict}; next={mission.stage.value}[/dim]"
    )
    if mission.stage is MissionStage.FAILED:
        raise typer.Exit(1)


@mission_app.command("review")
def mission_review(
    mission_id: str = typer.Argument(..., help="Mission that passed candidate verification."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Run the deterministic candidate diff review gate without executing code."""
    from .mission_models import MissionStage
    from .mission_workflow import MissionWorkflow

    try:
        mission = MissionWorkflow(root).review_candidate(mission_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    report = mission.artifacts.review_report
    console.print(
        f"[green]review recorded[/green] [cyan]{mission.id}[/cyan] "
        f"[dim]verdict={report.verdict if report else 'unknown'}; next={mission.stage.value}[/dim]"
    )
    if mission.stage is MissionStage.IMPLEMENTING:
        raise typer.Exit(1)


@mission_app.command("security")
def mission_security(
    mission_id: str = typer.Argument(..., help="Mission that passed structural review."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Scan candidate-added code for masked secret evidence before approval."""
    from .mission_models import MissionStage
    from .mission_workflow import MissionWorkflow

    try:
        mission = MissionWorkflow(root).scan_candidate_security(mission_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    report = mission.artifacts.security_scan
    console.print(
        f"[green]security scan recorded[/green] [cyan]{mission.id}[/cyan] "
        f"[dim]verdict={report.verdict if report else 'unknown'}; next={mission.stage.value}[/dim]"
    )
    if mission.stage is MissionStage.IMPLEMENTING:
        raise typer.Exit(1)


@mission_app.command("evaluate")
def mission_evaluate(
    mission_id: str = typer.Argument(..., help="Saved mission id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Run and retain the deterministic release-readiness evaluation gate."""
    from .mission_workflow import MissionWorkflow

    try:
        mission = MissionWorkflow(root).evaluate(mission_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    _print_mission_evaluation(mission.artifacts.evaluation_gate)
    if not mission.artifacts.evaluation_gate or not mission.artifacts.evaluation_gate.eligible:
        raise typer.Exit(1)


@mission_app.command("draft-pr")
def mission_draft_pr(
    mission_id: str = typer.Argument(..., help="Mission awaiting human approval."),
    approved_by: str = typer.Option(..., "--approved-by", help="Human approving the verified mission."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    yes: bool = typer.Option(False, "--yes", help="Confirm preparation of a local PR draft."),
) -> None:
    """Create a local PR draft after the evaluation gate and explicit approval.

    It never creates a branch, commit, GitHub PR, or remote side effect.
    """
    if not yes:
        console.print("[yellow]not drafted[/yellow] (rerun with --yes after reviewing the evaluation gate)")
        raise typer.Exit(2)
    from .mission_workflow import MissionWorkflow

    try:
        mission = MissionWorkflow(root).prepare_pull_request_draft(mission_id, approved_by=approved_by)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    _print_pr_draft(mission.artifacts.pull_request_draft)


@mission_workspace_app.command("create")
def mission_workspace_create(
    mission_id: str = typer.Argument(..., help="Mission that owns this candidate checkout."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    image: str = typer.Option("", "--image", help="Required later for Docker-only candidate commands."),
    base_revision: Optional[str] = typer.Option(None, "--base-revision", help="Committed Git SHA; defaults to HEAD."),
) -> None:
    """Create a detached candidate checkout without executing code."""
    from .candidate_workspace import CandidateWorkspaceService
    from .mission_store import MissionStore
    from .worktree import NotAGitRepo

    missions = MissionStore(root)
    candidates = CandidateWorkspaceService(root)
    try:
        mission = missions.load(mission_id)
        candidate = candidates.create(mission.id, image=image, base_revision=base_revision)
        try:
            missions.set_candidate_workspace(mission.id, candidate.id)
        except Exception:
            candidates.destroy(candidate.id)
            raise
    except (NotAGitRepo, ValueError) as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(
        f"[green]candidate ready[/green] [cyan]{candidate.id}[/cyan] for [cyan]{mission.id}[/cyan] "
        f"[dim](base {candidate.base_revision[:12]}; detached checkout; no command ran)[/dim]"
    )


@mission_workspace_app.command("list")
def mission_workspace_list(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Maximum candidates to show."),
) -> None:
    """List candidate workspaces without revealing or modifying their contents."""
    from .candidate_workspace import CandidateWorkspaceService

    candidates = CandidateWorkspaceService(root).list(limit=limit)
    if not candidates:
        console.print("[dim]no candidate workspaces saved for this project.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Candidate", no_wrap=True)
    table.add_column("Mission", no_wrap=True)
    table.add_column("State")
    table.add_column("Image")
    table.add_column("Base")
    for candidate in candidates:
        table.add_row(
            candidate.id,
            candidate.mission_id,
            candidate.status,
            candidate.image or "not set",
            candidate.base_revision[:12],
        )
    console.print(table)


@mission_workspace_app.command("diff")
def mission_workspace_diff(
    candidate_id: str = typer.Argument(..., help="Active candidate workspace id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Show the active candidate's isolated diff; the parent checkout is untouched."""
    from .candidate_workspace import CandidateWorkspaceService

    try:
        diff = CandidateWorkspaceService(root).diff(candidate_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(diff or "[dim]candidate checkout has no changes.[/dim]", markup=False)


@mission_workspace_app.command("run")
def mission_workspace_run(
    candidate_id: str = typer.Argument(..., help="Active candidate workspace id."),
    command: str = typer.Argument(..., help="Command to run inside the candidate Docker container."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    timeout: int = typer.Option(600, "--timeout", min=1, max=3_600, help="Docker command deadline in seconds."),
    yes: bool = typer.Option(False, "--yes", help="Confirm the candidate command."),
) -> None:
    """Run one command only in the configured, network-disabled Docker candidate."""
    if not yes:
        console.print("[yellow]not run[/yellow] (review the candidate and rerun with --yes)")
        raise typer.Exit(2)
    from .candidate_workspace import CandidateWorkspaceService

    try:
        result = CandidateWorkspaceService(root).run_command(candidate_id, command, timeout=timeout)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    if result.stdout:
        console.print(result.stdout, end="", markup=False)
    if result.stderr:
        console.print(result.stderr, end="", markup=False)
    if result.passed:
        console.print("[green]candidate command passed[/green]")
        return
    console.print(f"[red]candidate command did not pass[/red] {result.error or ''}")
    raise typer.Exit(1)


@mission_workspace_app.command("destroy")
def mission_workspace_destroy(
    candidate_id: str = typer.Argument(..., help="Candidate workspace id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    yes: bool = typer.Option(False, "--yes", help="Confirm permanent removal of this detached candidate checkout."),
) -> None:
    """Destroy one disposable candidate checkout while retaining terminal metadata."""
    if not yes:
        console.print("[yellow]not destroyed[/yellow] (rerun with --yes after reviewing its diff)")
        raise typer.Exit(2)
    from .candidate_workspace import CandidateWorkspaceService

    try:
        candidate = CandidateWorkspaceService(root).destroy(candidate_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]destroyed[/green] [cyan]{candidate.id}[/cyan]")


@mission_worker_app.command("auth-init")
def mission_worker_auth_init(
    root: Path = typer.Option(Path("."), "--root", help="Controller project directory."),
    rotate: bool = typer.Option(False, "--rotate", help="Replace the existing worker token."),
    yes: bool = typer.Option(False, "--yes", help="Confirm token creation or rotation."),
) -> None:
    """Create the controller token once; store only a salted digest locally."""
    if not yes:
        console.print("[yellow]not initialized[/yellow] (rerun with --yes after choosing where workers receive the token)")
        raise typer.Exit(2)
    from .remote_workers import RemoteWorkerAuth

    try:
        token = RemoteWorkerAuth(root).provision(rotate=rotate)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print("[green]remote worker token created[/green] [bold]shown once below[/bold]")
    console.print(token, markup=False)
    console.print("[dim]Set this as RONIN_WORKER_TOKEN on each trusted worker; it is not stored in clear text.[/dim]")


@mission_worker_app.command("enqueue")
def mission_worker_enqueue(
    mission_id: str = typer.Argument(..., help="Mission in implementing state with an active candidate."),
    command: str = typer.Argument(..., help="Verification command that the remote Docker worker may run."),
    root: Path = typer.Option(Path("."), "--root", help="Controller project directory."),
    repository_url: Optional[str] = typer.Option(
        None, "--repository-url", help="Credential-free HTTPS clone URL; defaults to origin.",
    ),
    timeout: int = typer.Option(600, "--timeout", min=1, max=3_600, help="Remote Docker deadline in seconds."),
) -> None:
    """Snapshot a candidate patch for remote verification; this does not run it."""
    from .remote_workers import RemoteVerificationCoordinator

    try:
        job = RemoteVerificationCoordinator(root).enqueue(
            mission_id, command, timeout_seconds=timeout, repository_url=repository_url,
        )
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(
        f"[green]queued[/green] [cyan]{job.id}[/cyan] for [bold]{job.mission_id}[/bold] "
        f"[dim](patch {job.patch_digest[:12]}; no worker started)[/dim]"
    )


@mission_worker_app.command("list")
def mission_worker_list(
    root: Path = typer.Option(Path("."), "--root", help="Controller project directory."),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Maximum recent jobs."),
) -> None:
    """List remote verification lifecycle state without revealing patch contents."""
    from .remote_workers import RemoteWorkerStore

    jobs = RemoteWorkerStore(root).list(limit=limit)
    if not jobs:
        console.print("[dim]no remote verification jobs saved for this project.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Job", no_wrap=True)
    table.add_column("Status")
    table.add_column("Worker")
    table.add_column("Attempts", justify="right")
    table.add_column("Mission", no_wrap=True)
    table.add_column("Evidence")
    for job in jobs:
        table.add_row(
            job.id, job.status, job.worker_id or "-", str(job.attempts), job.mission_id,
            job.evidence.outcome if job.evidence else "pending",
        )
    console.print(table)


@mission_worker_app.command("release")
def mission_worker_release(
    job_id: str = typer.Argument(..., help="Leased job whose worker has definitely stopped."),
    root: Path = typer.Option(Path("."), "--root", help="Controller project directory."),
    yes: bool = typer.Option(False, "--yes", help="Confirm that the leased worker is no longer running."),
) -> None:
    """Explicitly requeue an interrupted worker lease; it never starts a worker."""
    if not yes:
        console.print("[yellow]not released[/yellow] (confirm the worker stopped, then rerun with --yes)")
        raise typer.Exit(2)
    from .remote_workers import RemoteWorkerStore

    try:
        job = RemoteWorkerStore(root).release(job_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]requeued[/green] [cyan]{job.id}[/cyan]")


@mission_worker_app.command("run")
def mission_worker_run(
    endpoint: str = typer.Option(..., "--endpoint", help="HTTPS controller endpoint; localhost HTTP is allowed for development."),
    worker_id: str = typer.Option(..., "--worker-id", help="Stable id for this worker."),
    token_env: str = typer.Option("RONIN_WORKER_TOKEN", "--token-env", help="Environment variable containing the controller token."),
    yes: bool = typer.Option(False, "--yes", help="Confirm clone, patch application, and Docker-only execution."),
) -> None:
    """Claim one job, run it in a disposable Docker checkout, and return bounded evidence."""
    if not yes:
        console.print("[yellow]not run[/yellow] (rerun with --yes after reviewing the trusted controller endpoint)")
        raise typer.Exit(2)
    token = os.environ.get(token_env, "")
    if not token:
        console.print(f"[red]x[/red] worker token environment variable {token_env} is not set")
        raise typer.Exit(2)
    from .remote_workers import RemoteWorkerClient

    try:
        job = RemoteWorkerClient(endpoint, token).run_once(worker_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    if job is None:
        console.print("[dim]no remote verification job is ready.[/dim]")
        return
    outcome = job.evidence.outcome if job.evidence else "unknown"
    mark = "[green]completed[/green]" if job.status == "completed" else "[red]failed[/red]"
    console.print(f"{mark} [cyan]{job.id}[/cyan] [dim]evidence={outcome}[/dim]")
    if job.status != "completed":
        raise typer.Exit(1)


def _print_mission_evaluation(gate) -> None:
    if gate is None:
        console.print("[yellow]no mission evaluation has been recorded[/yellow]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Gate")
    table.add_column("Status")
    table.add_column("Evidence", overflow="fold")
    for check in gate.checks:
        table.add_row(check.name, check.status, check.detail)
    console.print(table)
    mark = "[green]eligible[/green]" if gate.eligible else "[red]not eligible[/red]"
    console.print(f"release evaluation: {mark}")


def _print_pr_draft(draft) -> None:
    if draft is None:
        console.print("[yellow]no pull-request draft was recorded[/yellow]")
        return
    console.print(f"[green]local PR draft ready[/green] [bold]{draft.title}[/bold]")
    console.print(f"[dim]suggested branch: {draft.suggested_branch}; base: {draft.base_revision[:12]}[/dim]")
    if draft.files_changed:
        console.print(f"[dim]files: {', '.join(draft.files_changed)}[/dim]")
    if draft.body:
        console.print(draft.body, markup=False)


def _print_mission(mission) -> None:
    audit = Table(box=box.SIMPLE, show_header=False)
    audit.add_column("Field", style="cyan", no_wrap=True)
    audit.add_column("Value")
    audit.add_row("mission", mission.id)
    audit.add_row("stage", mission.stage.value)
    audit.add_row("source", f"{mission.spec.source}:{mission.spec.source_id}".rstrip(":"))
    audit.add_row("candidate", mission.candidate_workspace_id or "not created")
    audit.add_row("events", str(mission.event_count))
    audit.add_row("plan", "recorded" if mission.artifacts.plan else "not recorded")
    audit.add_row("tests", mission.artifacts.test_report.verdict if mission.artifacts.test_report else "not recorded")
    audit.add_row("review", mission.artifacts.review_report.verdict if mission.artifacts.review_report else "not recorded")
    audit.add_row("security", mission.artifacts.security_scan.verdict if mission.artifacts.security_scan else "not recorded")
    audit.add_row("evaluation", "eligible" if mission.artifacts.evaluation_gate and mission.artifacts.evaluation_gate.eligible else "not eligible")
    audit.add_row("PR draft", mission.artifacts.pull_request_draft.status if mission.artifacts.pull_request_draft else "not recorded")
    audit.add_row("title", mission.spec.title)
    console.print(audit)
    if mission.spec.acceptance_criteria:
        console.print("[bold]acceptance criteria[/bold]")
        for criterion in mission.spec.acceptance_criteria:
            console.print(f"  - {criterion}")


fleet_app = typer.Typer(
    help="Create and inspect bounded multi-wave specialist fleet plans.",
    no_args_is_help=True,
)
util_app.add_typer(fleet_app, name="fleet")


@fleet_app.command("plan")
def fleet_plan(
    goal: str = typer.Argument(..., help="Goal used to select and schedule specialist profiles."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    write: bool = typer.Option(False, "--write", help="Include a planned implementation phase; does not permit writes."),
    max_profiles: int = typer.Option(32, "--max-profiles", min=4, max=512, help="Maximum relevant profiles across all waves."),
    max_parallel: int = typer.Option(8, "--max-parallel", min=1, max=32, help="Maximum profiles scheduled in one wave."),
    agent_manifest: Optional[Path] = typer.Option(None, "--agent-manifest", help="Project profile manifest (default: .ronin/agents.json)."),
) -> None:
    """Persist a local multi-wave plan without starting agents or modifying code."""
    from .agent_catalog import repository_signals
    from .agent_fleet import FleetPlanStore, plan_fleet
    from .orchestrate import agent_catalog, core_agent_profiles

    try:
        catalog = agent_catalog(root, manifest=agent_manifest)
        plan = plan_fleet(
            goal, catalog, core_keys=(profile.key for profile in core_agent_profiles()),
            repository=repository_signals(goal, root), write=write,
            max_profiles=max_profiles, max_parallel=max_parallel,
        )
        FleetPlanStore(root).save(plan)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    _print_fleet_plan(plan)
    console.print(
        f"[green]saved[/green] [cyan]{plan.id}[/cyan] "
        "[dim](a plan only; no agent, command, or write was started)[/dim]"
    )


@fleet_app.command("list")
def fleet_list(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Maximum recent plans to show."),
) -> None:
    """List saved project-local fleet plans."""
    from .agent_fleet import FleetPlanStore

    plans = FleetPlanStore(root).list(limit=limit)
    if not plans:
        console.print("[dim]no saved fleet plans.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Plan", no_wrap=True)
    table.add_column("Mode")
    table.add_column("Profiles", justify="right")
    table.add_column("Waves", justify="right")
    table.add_column("Goal", overflow="fold")
    for plan in plans:
        table.add_row(
            plan.id, "write plan" if plan.write else "read plan", str(len(plan.members)),
            str(len(plan.waves)), plan.goal,
        )
    console.print(table)


@fleet_app.command("show")
def fleet_show(
    plan_id: str = typer.Argument(..., help="Saved fleet plan id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Show an inspectable phase/wave schedule without starting any agent."""
    from .agent_fleet import FleetPlanStore

    try:
        plan = FleetPlanStore(root).load(plan_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    _print_fleet_plan(plan)


@fleet_app.command("start")
def fleet_start(
    plan_id: str = typer.Argument(..., help="Saved fleet plan id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Create a durable fleet run without starting a provider or worker."""
    from .agent_fleet import FleetPlanStore
    from .agent_fleet_runs import FleetRunStore

    try:
        plan = FleetPlanStore(root).load(plan_id)
        run = FleetRunStore(root).create(plan)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(
        f"[green]started[/green] [cyan]{run.id}[/cyan] "
        "[dim](no worker started; run a bounded wave with `ronin util fleet run-next`)[/dim]"
    )


@fleet_app.command("runs")
def fleet_runs(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Maximum recent fleet runs to show."),
) -> None:
    """List durable fleet execution instances and their current phase."""
    from .agent_fleet_runs import FleetRunStore

    runs = FleetRunStore(root).list(limit=limit)
    if not runs:
        console.print("[dim]no fleet runs. Start one from a saved fleet plan.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Run", no_wrap=True)
    table.add_column("Status")
    table.add_column("Progress")
    table.add_column("Plan", no_wrap=True)
    table.add_column("Goal", overflow="fold")
    for run in runs:
        done = sum(wave.status == "completed" for wave in run.waves)
        active = next((wave for wave in run.waves if wave.status == "running"), None)
        progress = f"{done}/{len(run.waves)}"
        if active is not None:
            progress += f" (wave {active.number} {active.phase})"
        table.add_row(run.id, run.status, progress, run.plan_id, run.goal)
    console.print(table)


@fleet_app.command("run-next")
def fleet_run_next(
    run_id: str = typer.Argument(..., help="Fleet run id created by `fleet start`."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    roster: str = typer.Option(None, "--roster", "-r", help="Optional role=provider[:model] overrides."),
    agent_manifest: Optional[Path] = typer.Option(
        None, "--agent-manifest", help="The project profile manifest used when the plan was created.",
    ),
    offline: bool = typer.Option(False, "--offline", help="Run this wave on a local provider only."),
    max_subtask_iterations: int = typer.Option(12, "--max-subtask-iterations", min=1, max=100),
    agent_timeout: float = typer.Option(300.0, "--agent-timeout", min=1.0, help="Wall-clock seconds allowed for each sub-agent."),
) -> None:
    """Claim and execute one dependency-ready fleet wave.

    This is intentionally one wave at a time. It preserves the saved fleet
    parallelism cap and leaves each completed write wave as a reviewable
    isolated-worktree proposal; it never stages, commits, or pushes a change.
    """
    from .agent_fleet_runs import FleetRunStore, FleetWaveResult, execute_next_wave
    from .agent_governance import OrchestrationGovernance
    from .offline import apply_offline
    from .orchestrate import agent_catalog, run_orchestrate

    config = load_config()
    if offline:
        config = apply_offline(config.model_copy(update={
            "provider": "local", "model": None, "base_url": None,
            "failover": [], "offline": True,
        }))
    elif not config.has_provider_auth():
        console.print(f"[red]x[/red] no credentials for {config.provider}; use --offline or configure a provider")
        raise typer.Exit(2)

    store = FleetRunStore(root)
    captured: dict[str, object] = {}

    def worker(fleet_run, wave):
        catalog = {profile.key: profile for profile in agent_catalog(root, manifest=agent_manifest)}
        missing = [key for key in wave.profile_keys if key not in catalog]
        if missing:
            return FleetWaveResult(False, error="fleet profiles are no longer available: " + ", ".join(missing))
        profiles = tuple(catalog[key] for key in wave.profile_keys)
        write_wave = fleet_run.write and wave.phase == "implementation"
        governance = OrchestrationGovernance(
            max_agents=len(profiles),
            max_parallel=min(len(profiles), fleet_run.max_parallel),
            max_iterations_per_agent=max_subtask_iterations,
            max_total_subtask_iterations=max(len(profiles) * max_subtask_iterations, max_subtask_iterations),
            max_wall_time_seconds_per_agent=agent_timeout,
        )
        completed = [
            f"wave {item.number} ({item.phase}) run {item.agent_run_id or 'recorded'}"
            for item in fleet_run.waves if item.status == "completed"
        ]
        handoff = "; ".join(completed) if completed else "no earlier fleet waves"
        wave_goal = (
            f"{fleet_run.goal}\n\n"
            f"Fleet execution: wave {wave.number} ({wave.phase}). "
            f"Assigned specialists: {', '.join(wave.profile_keys)}. "
            f"Earlier completed work: {handoff}. "
            "Work only within this wave's role and phase. Re-read repository evidence; "
            "do not treat prior work as verified."
        )
        outcome = run_orchestrate(
            config, wave_goal, roster_spec=roster, root=root,
            read_only=not write_wave, profiles=profiles, max_agents=len(profiles),
            governance=governance,
        )
        captured["outcome"] = outcome
        proposal = outcome.proposal if isinstance(outcome.proposal, dict) else {}
        return FleetWaveResult(
            outcome.success,
            agent_run_id=outcome.run_id,
            proposal_run_id=proposal.get("run_id") if isinstance(proposal.get("run_id"), str) else None,
            error=outcome.error,
        )

    try:
        run, wave = execute_next_wave(store, run_id, worker)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    if wave is None:
        console.print(f"[dim]fleet run {run.id} is {run.status}; no ready wave remains.[/dim]")
        if run.error:
            console.print(f"[red]{run.error}[/red]")
        return
    finished = next(item for item in run.waves if item.number == wave.number)
    mark = "[green]completed[/green]" if finished.status == "completed" else "[red]failed[/red]"
    console.print(f"{mark} fleet wave {finished.number} ({finished.phase}) in [cyan]{run.id}[/cyan]")
    if finished.agent_run_id:
        console.print(f"[dim]agent run: {finished.agent_run_id}[/dim]")
    if finished.proposal_run_id:
        console.print(
            f"[dim]review proposal: `ronin util proposals show {finished.proposal_run_id} --role <role>`[/dim]"
        )
    outcome = captured.get("outcome")
    if outcome is not None and getattr(outcome, "output", ""):
        console.print(getattr(outcome, "output"))
    if finished.status != "completed":
        console.print(f"[red]{finished.error or run.error or 'wave failed'}[/red]")
        raise typer.Exit(1)


@fleet_app.command("retry")
def fleet_retry(
    run_id: str = typer.Argument(..., help="Fleet run id."),
    wave_number: int = typer.Argument(..., min=1, help="Failed wave number to make eligible again."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    yes: bool = typer.Option(False, "--yes", help="Confirm that the failed wave was reviewed before retrying."),
) -> None:
    """Make one failed wave eligible for an explicit rerun; it does not start it."""
    if not yes:
        console.print("[yellow]not retried[/yellow] (review the failed wave, then rerun with --yes)")
        raise typer.Exit(2)
    from .agent_fleet_runs import FleetRunStore

    try:
        run = FleetRunStore(root).retry_failed(run_id, wave_number)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]wave {wave_number} is ready[/green] in [cyan]{run.id}[/cyan]")


@fleet_app.command("recover")
def fleet_recover(
    run_id: str = typer.Argument(..., help="Fleet run id with a worker that was interrupted."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    yes: bool = typer.Option(False, "--yes", help="Confirm the previous worker has stopped before releasing its claim."),
) -> None:
    """Release an interrupted active wave so an operator may explicitly rerun it."""
    if not yes:
        console.print("[yellow]not recovered[/yellow] (confirm the previous worker stopped, then rerun with --yes)")
        raise typer.Exit(2)
    from .agent_fleet_runs import FleetRunStore

    try:
        run = FleetRunStore(root).recover_interrupted(run_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]released interrupted wave[/green] in [cyan]{run.id}[/cyan]")


@fleet_app.command("cancel")
def fleet_cancel(
    run_id: str = typer.Argument(..., help="Fleet run id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    yes: bool = typer.Option(False, "--yes", help="Confirm cancellation of waiting fleet waves."),
) -> None:
    """Cancel waiting waves without changing completed evidence or active work."""
    if not yes:
        console.print("[yellow]not cancelled[/yellow] (rerun with --yes to cancel waiting waves)")
        raise typer.Exit(2)
    from .agent_fleet_runs import FleetRunStore

    try:
        run = FleetRunStore(root).cancel(run_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]cancelled[/green] [cyan]{run.id}[/cyan]")


def _print_fleet_plan(plan) -> None:
    console.print(
        f"[bold]fleet plan[/bold] {plan.id} [dim]catalog={plan.catalog_size}, "
        f"selected={len(plan.members)}, wave cap={plan.max_parallel}[/dim]"
    )
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Wave", justify="right")
    table.add_column("Phase")
    table.add_column("Active", justify="right")
    table.add_column("Waits for")
    table.add_column("Profiles", overflow="fold")
    for wave in plan.waves:
        waits_for = ", ".join(str(number) for number in wave.waits_for) or "-"
        table.add_row(
            str(wave.number), wave.phase, str(wave.parallelism), waits_for,
            ", ".join(wave.profile_keys),
        )
    console.print(table)
    if plan.repository.tags:
        console.print(f"[dim]routing evidence: {', '.join(plan.repository.tags)}[/dim]")


persistent_team_app = typer.Typer(
    help="Operate durable local specialist identities, experience ledgers, and recoverable assignments.",
    no_args_is_help=True,
)
util_app.add_typer(persistent_team_app, name="team")


@persistent_team_app.command("init")
def persistent_team_init(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Create the default persistent architect, implementer, reviewer, tester, security, and release roles."""
    from .persistent_agents import PersistentAgentStore

    agents = PersistentAgentStore(root).bootstrap()
    console.print(f"[green]ready[/green] persistent team with {len(agents)} local role identities")
    _print_persistent_agents(agents)


@persistent_team_app.command("status")
def persistent_team_status(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Show safe role lifecycle and health metadata; task text and experience stay local."""
    from .persistent_agents import PersistentAgentStore

    agents = PersistentAgentStore(root).list()
    if not agents:
        console.print("[dim]no persistent team yet; run `ronin util team init`.[/dim]")
        return
    _print_persistent_agents(agents)


@persistent_team_app.command("assign")
def persistent_team_assign(
    agent_id: str = typer.Argument(..., help="Idle persistent role id, for example architect-01."),
    mission_id: str = typer.Argument(..., help="Mission id this role will serve."),
    task: str = typer.Argument(..., help="Bounded role-specific task description."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Reserve one idle role for a mission without starting a provider or worker."""
    from .persistent_agents import PersistentAgentStore

    try:
        agent = PersistentAgentStore(root).assign(agent_id, mission_id, task)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]assigned[/green] [cyan]{agent.agent_id}[/cyan] to {agent.current_mission_id}")


@persistent_team_app.command("start")
def persistent_team_start(
    agent_id: str = typer.Argument(..., help="Assigned persistent role id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Mark an assigned role as running after the governed worker claims it."""
    from .persistent_agents import PersistentAgentStore

    try:
        agent = PersistentAgentStore(root).start(agent_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]running[/green] [cyan]{agent.agent_id}[/cyan]")


@persistent_team_app.command("complete")
def persistent_team_complete(
    agent_id: str = typer.Argument(..., help="Running persistent role id."),
    summary: str = typer.Option("", "--summary", help="Bounded operator-facing outcome summary."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Record completion evidence; release remains an explicit separate action."""
    from .persistent_agents import PersistentAgentStore

    try:
        agent = PersistentAgentStore(root).complete(agent_id, summary)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]completed[/green] [cyan]{agent.agent_id}[/cyan]")


@persistent_team_app.command("handoff")
def persistent_team_handoff(
    source_agent_id: str = typer.Argument(..., help="Completed persistent role id that produced the evidence."),
    recipient_agent_id: str = typer.Argument(..., help="Assigned persistent role id that will receive the evidence."),
    mission_id: str = typer.Argument(..., help="Mission shared by both persistent roles."),
    summary: str = typer.Option(..., "--summary", help="Bounded handoff conclusion; raw chat transcripts are not accepted."),
    evidence: list[str] = typer.Option([], "--evidence", help="Repeatable typed reference: plan:ID, test:ID, review:ID, security:ID, or candidate:ID."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Create a compact evidence handoff from a completed role to an assigned peer."""
    from .persistent_agents import PersistentAgentStore

    try:
        evidence_entries = tuple(_parse_persistent_handoff_evidence(value) for value in evidence)
        handoff = PersistentAgentStore(root).create_handoff(
            source_agent_id, recipient_agent_id, mission_id, summary, evidence_entries,
        )
    except (TypeError, ValueError) as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(
        f"[green]handed off[/green] [cyan]{handoff.handoff_id}[/cyan] "
        f"from {handoff.from_agent_id} to {handoff.to_agent_id} ({len(handoff.evidence)} evidence reference(s))",
    )


@persistent_team_app.command("accept-handoff")
def persistent_team_accept_handoff(
    handoff_id: str = typer.Argument(..., help="Pending persistent handoff id."),
    recipient_agent_id: str = typer.Argument(..., help="Assigned recipient role id that acknowledges the evidence."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Explicitly acknowledge a handoff without starting the recipient's worker."""
    from .persistent_agents import PersistentAgentStore

    try:
        handoff = PersistentAgentStore(root).acknowledge_handoff(handoff_id, recipient_agent_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]acknowledged[/green] [cyan]{handoff.handoff_id}[/cyan] by {handoff.to_agent_id}")


@persistent_team_app.command("handoffs")
def persistent_team_handoffs(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
) -> None:
    """List persistent evidence handoffs without printing their summaries or references."""
    from .persistent_agents import PersistentAgentStore

    handoffs = PersistentAgentStore(root).list_handoffs(limit=limit)
    if not handoffs:
        console.print("[dim]no persistent team handoffs yet.[/dim]")
        return
    _print_persistent_handoffs(handoffs)


@persistent_team_app.command("release")
def persistent_team_release(
    agent_id: str = typer.Argument(..., help="Completed or crashed persistent role id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Return a reviewed terminal role to the idle pool; no work is discarded."""
    from .persistent_agents import PersistentAgentStore

    try:
        agent = PersistentAgentStore(root).release(agent_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]idle[/green] [cyan]{agent.agent_id}[/cyan]")


@persistent_team_app.command("heartbeat")
def persistent_team_heartbeat(
    agent_id: str = typer.Argument(..., help="Persistent role id whose worker is alive."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Record a worker heartbeat without changing its lifecycle state."""
    from .persistent_agents import PersistentAgentStore

    try:
        agent = PersistentAgentStore(root).heartbeat(agent_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]healthy[/green] [cyan]{agent.agent_id}[/cyan] ({agent.status})")


@persistent_team_app.command("supervise")
def persistent_team_supervise(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    heartbeat_age: int = typer.Option(30, "--heartbeat-age", min=1, max=86_400, help="Seconds before an in-flight role is stale."),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-auto-restart", help="Return stale assigned/running roles to recoverable assigned state."),
) -> None:
    """Detect stale role heartbeats and preserve their mission assignment for recovery."""
    from .persistent_agents import PersistentAgentStore

    try:
        changed = PersistentAgentStore(root).supervise(
            max_age_seconds=heartbeat_age, auto_restart=auto_restart,
        )
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    if not changed:
        console.print("[green]healthy[/green] no stale persistent role assignments")
        return
    _print_persistent_agents(changed)


persistent_team_memory_app = typer.Typer(
    help="Manage source-attributed local experience ledgers for persistent roles.",
    no_args_is_help=True,
)
persistent_team_app.add_typer(persistent_team_memory_app, name="memory")


@persistent_team_memory_app.command("remember")
def persistent_team_memory_remember(
    agent_id: str = typer.Argument(..., help="Persistent role id."),
    content: str = typer.Argument(..., help="Durable repo-specific learning; likely secrets are refused."),
    source_type: str = typer.Option(..., "--source-type", help="mission, issue, pull_request, commit, human, skill, adr, or workaround."),
    source_id: str = typer.Option(..., "--source-id", help="Immutable source identifier for the learning."),
    confidence: float = typer.Option(0.7, "--confidence", min=0.0, max=1.0),
    expires_at: Optional[str] = typer.Option(None, "--expires-at", help="ISO timestamp; defaults depend on provenance."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Store one local role experience with provenance, confidence, and expiry."""
    from .persistent_agents import PersistentAgentStore

    try:
        entry = PersistentAgentStore(root).remember(
            agent_id, content, source_type=source_type.strip().lower(), source_id=source_id,
            confidence=confidence, expires_at=expires_at,
        )
    except (ValueError, TypeError) as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]remembered[/green] [cyan]{entry.memory_id}[/cyan] until {entry.expires_at}")


@persistent_team_memory_app.command("recall")
def persistent_team_memory_recall(
    agent_id: str = typer.Argument(..., help="Persistent role id."),
    query: str = typer.Argument(..., help="Experience query."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    limit: int = typer.Option(10, "--limit", min=1, max=50),
) -> None:
    """Recall active role experience by local lexical relevance."""
    from .persistent_agents import PersistentAgentStore

    try:
        entries = PersistentAgentStore(root).recall(agent_id, query, limit=limit)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    if not entries:
        console.print("[dim]no matching active role experience.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Source")
    table.add_column("Confidence", justify="right")
    table.add_column("Experience", overflow="fold")
    for entry in entries:
        table.add_row(f"{entry.source_type}:{entry.source_id}", f"{entry.confidence:.2f}", entry.content)
    console.print(table)


@persistent_team_memory_app.command("compact")
def persistent_team_memory_compact(
    agent_id: str = typer.Argument(..., help="Persistent role id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Archive expired low-confidence experience and retain a compact historical summary."""
    from .persistent_agents import PersistentAgentStore

    try:
        result = PersistentAgentStore(root).compact_memories(agent_id)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    if not result.archived:
        console.print("[dim]no expired low-confidence experience to compact.[/dim]")
        return
    console.print(f"[green]compacted[/green] {result.archived} memories into {result.summary_memory_id}")


@persistent_team_app.command("context")
def persistent_team_context(
    agent_id: str = typer.Argument(..., help="Persistent role id."),
    task: str = typer.Argument(..., help="Task used to retrieve bounded context."),
    mission_id: str = typer.Option("", "--mission", help="Optional mission correlation id."),
    max_tokens: int = typer.Option(2_000, "--max-tokens", min=64, max=32_000),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Print a local token-bounded context pack; no provider is called."""
    from .persistent_agents import PersistentAgentStore

    try:
        pack = PersistentAgentStore(root).context_pack(
            agent_id, task, mission_id=mission_id, max_tokens=max_tokens,
        )
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print_json(pack.model_dump_json(indent=2))


@persistent_team_app.command("audit")
def persistent_team_audit(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Verify the persistent-team supervisor's compact append-only audit chain."""
    from .persistent_agents import PersistentAgentStore

    report = PersistentAgentStore(root).verify_audit()
    if not report.valid:
        console.print(f"[red]invalid[/red] persistent team audit: {report.error or 'unknown error'}")
        raise typer.Exit(1)
    console.print(f"[green]valid[/green] persistent team audit ({report.events} event(s))")


def _print_persistent_agents(agents) -> None:
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Agent", no_wrap=True)
    table.add_column("Role")
    table.add_column("Status")
    table.add_column("Mission", no_wrap=True)
    table.add_column("Restarts", justify="right")
    table.add_column("Last health")
    for agent in agents:
        table.add_row(
            agent.agent_id, agent.role, agent.status, agent.current_mission_id or "-",
            str(agent.restart_count), agent.health_check_at,
        )
    console.print(table)


def _parse_persistent_handoff_evidence(value: str):
    from .persistent_agents import HandoffEvidenceInput

    kind, separator, reference = value.partition(":")
    if not separator or not kind.strip() or not reference.strip():
        raise ValueError("handoff evidence must use kind:reference")
    return HandoffEvidenceInput(kind=kind.strip().lower(), reference=reference.strip())


def _print_persistent_handoffs(handoffs) -> None:
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Handoff", no_wrap=True)
    table.add_column("Mission", no_wrap=True)
    table.add_column("From")
    table.add_column("To")
    table.add_column("Status")
    table.add_column("Evidence", justify="right")
    table.add_column("Updated")
    for handoff in handoffs:
        table.add_row(
            handoff.handoff_id, handoff.mission_id, handoff.from_agent_id, handoff.to_agent_id,
            handoff.status, str(len(handoff.evidence)), handoff.updated_at,
        )
    console.print(table)


agent_queue_app = typer.Typer(
    help="Project-local queue for governed orchestration jobs. Jobs run only when a worker claims them.",
    no_args_is_help=True,
)
util_app.add_typer(agent_queue_app, name="agent-queue")


@agent_queue_app.command("add")
def agent_queue_add(
    goal: str = typer.Argument(..., help="Goal to run through the agent control plane."),
    write: bool = typer.Option(False, "--write", help="Run in reviewed, isolated worktrees when claimed."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Queue a governed agent goal; this command never starts an agent by itself."""
    from .agent_queue import AgentQueue

    try:
        job = AgentQueue(root).enqueue(goal, write=write)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    mode = "write worktree" if write else "read-only"
    console.print(f"[green]queued[/green] [cyan]{job.id}[/cyan] [dim]({mode})[/dim]")


@agent_queue_app.command("list")
def agent_queue_list(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Show durable queued, running, and completed agent jobs for a project."""
    from .agent_queue import AgentQueue

    jobs = AgentQueue(root).list()
    if not jobs:
        console.print("[dim]no queued agent jobs.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Job", no_wrap=True)
    table.add_column("Status")
    table.add_column("Mode")
    table.add_column("Attempts", justify="right")
    table.add_column("Goal", overflow="fold")
    for job in jobs:
        table.add_row(job.id, job.status, "write" if job.write else "read", str(job.attempts), job.goal)
    console.print(table)


@agent_queue_app.command("cancel")
def agent_queue_cancel(
    job_id: str = typer.Argument(..., help="Queued job id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Cancel a waiting job. Running and terminal jobs stay immutable."""
    from .agent_queue import AgentQueue

    if AgentQueue(root).cancel(job_id) is None:
        console.print(f"[red]x[/red] no queued job named {job_id!r}")
        raise typer.Exit(1)
    console.print(f"[green]cancelled[/green] [cyan]{job_id}[/cyan]")


@agent_queue_app.command("run-next")
def agent_queue_run_next(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    offline: bool = typer.Option(False, "--offline", help="Run on a local provider only."),
) -> None:
    """Claim and execute one queued job with normal governance and approvals."""
    from .agent_queue import AgentQueue
    from .agent_workflow import workflow_for_goal
    from .orchestrate import run_orchestrate

    queue = AgentQueue(root)
    job = queue.claim_next()
    if job is None:
        console.print("[dim]no queued agent jobs.[/dim]")
        return
    config = load_config()
    if offline:
        # A queued offline worker must not depend on a localhost daemon from the
        # interactive profile. The embedded provider is process-local, and the
        # offline policy will retain it while stripping network tools.
        config = config.model_copy(update={
            "provider": "local", "model": None, "base_url": None,
            "failover": [], "offline": True,
        })
    if not offline and not config.has_provider_auth():
        message = f"no credentials for {config.provider}"
        queue.finish(job.id, success=False, error=message)
        console.print(f"[red]x[/red] {message}")
        raise typer.Exit(2)
    try:
        outcome = run_orchestrate(
            config, job.goal, root=root, read_only=not job.write,
            workflow=workflow_for_goal(job.goal, write=True) if job.write else None,
        )
    except Exception as exc:  # noqa: BLE001 - queue must keep a terminal outcome
        outcome = None
        error = f"{type(exc).__name__}: {exc}"
    else:
        error = outcome.error
    queue.finish(
        job.id, success=bool(outcome and outcome.success), error=error,
        run_id=outcome.run_id if outcome is not None else None,
    )
    if outcome is None or not outcome.success:
        console.print(f"[red]x[/red] job failed: {error or 'unknown error'}")
        raise typer.Exit(1)
    console.print(f"[green]completed[/green] [cyan]{job.id}[/cyan] [dim]run {outcome.run_id or 'not persisted'}[/dim]")
    console.print(outcome.output)


@agent_queue_app.command("run")
def agent_queue_run(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    max_jobs: int = typer.Option(4, "--max-jobs", min=1, max=100, help="Maximum queued jobs to claim."),
    max_parallel: int = typer.Option(2, "--max-parallel", min=1, max=32, help="Maximum jobs to execute together."),
    offline: bool = typer.Option(False, "--offline", help="Run each worker on a local provider only."),
) -> None:
    """Claim and run a bounded backlog batch; every job retains its own outcome."""
    from .agent_queue import AgentQueue, QueueExecution
    from .agent_workflow import workflow_for_goal
    from .orchestrate import run_orchestrate

    config = load_config()
    if offline:
        config = config.model_copy(update={
            "provider": "local", "model": None, "base_url": None,
            "failover": [], "offline": True,
        })
    if not offline and not config.has_provider_auth():
        console.print(f"[red]x[/red] no credentials for {config.provider}")
        raise typer.Exit(2)

    def worker(job):
        try:
            outcome = run_orchestrate(
                config, job.goal, root=root, read_only=not job.write,
                workflow=workflow_for_goal(job.goal, write=True) if job.write else None,
            )
            return QueueExecution(outcome.success, outcome.error, outcome.run_id)
        except Exception as exc:  # noqa: BLE001 - queue stores the terminal failure
            return QueueExecution(False, f"{type(exc).__name__}: {exc}")

    try:
        finished = AgentQueue(root).run_pending(worker, max_jobs=max_jobs, max_parallel=max_parallel)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    if not finished:
        console.print("[dim]no queued agent jobs.[/dim]")
        return
    complete = sum(job.status == "completed" for job in finished)
    console.print(f"[green]{complete} completed[/green], [red]{len(finished) - complete} failed[/red] from {len(finished)} claimed jobs")


constitution_app = typer.Typer(help="Repository-owned guardrails for agent work.", no_args_is_help=True)
util_app.add_typer(constitution_app, name="constitution")


@constitution_app.command("init")
def constitution_init(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Create a conservative, editable `.ronin/constitution.json` starter."""
    from .constitution import write_starter

    try:
        path = write_starter(root)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(1)
    console.print(f"[green]created[/green] {path}")


@constitution_app.command("show")
def constitution_show(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Print the effective local constitution, including the permissive default."""
    from .constitution import load_constitution

    try:
        policy = load_constitution(root)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    import json
    console.print_json(json.dumps(policy.as_dict()))


ledger_app = typer.Typer(help="Inspect the local, hash-chained autonomy ledger.", no_args_is_help=True)
util_app.add_typer(ledger_app, name="ledger")


@ledger_app.command("verify")
def ledger_verify(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Verify the ledger hash chain without contacting any external service."""
    from .autonomy_ledger import verify_ledger

    report = verify_ledger(root)
    if report.valid:
        console.print(f"[green]verified[/green] {report.events} ledger event(s)")
        return
    console.print(f"[red]invalid[/red] after {report.events} event(s): {report.error}")
    raise typer.Exit(1)


@ledger_app.command("show")
def ledger_show(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Maximum recent events."),
) -> None:
    """Show bounded event metadata. Goals and raw model output are never stored."""
    from .autonomy_ledger import recent_events

    rows = recent_events(root, limit=limit)
    if not rows:
        console.print("[dim]no autonomy ledger events.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("At")
    table.add_column("Kind")
    table.add_column("Run")
    table.add_column("Result")
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        table.add_row(str(row.get("at", "")), str(row.get("kind", "")), str(row.get("run_id") or "-"), str(payload.get("success", "-")))
    console.print(table)


project_memory_app = typer.Typer(help="Durable local project facts for human-approved agent recall.", no_args_is_help=True)
util_app.add_typer(project_memory_app, name="project-memory")


@project_memory_app.command("add")
def project_memory_add(
    text: str = typer.Argument(..., help="Durable project decision or fact."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    tags: str = typer.Option("", "--tags", help="Comma-separated retrieval tags."),
) -> None:
    """Add a local project fact. Likely credentials and private keys are refused."""
    from .project_memory import ProjectMemory

    try:
        record_id = ProjectMemory(root).remember(text, tags=[tag for tag in tags.split(",") if tag.strip()])
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]remembered[/green] {record_id}")


@project_memory_app.command("search")
def project_memory_search(
    query: str = typer.Argument(..., help="Fact or decision to recall."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    limit: int = typer.Option(5, "--limit", min=1, max=20, help="Maximum matching facts."),
) -> None:
    """Search project memory with deterministic local hashing embeddings."""
    from .project_memory import ProjectMemory

    rows = ProjectMemory(root).recall(query, k=limit)
    if not rows:
        console.print("[dim]no matching project memory.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Score")
    table.add_column("Fact", overflow="fold")
    for row in rows:
        table.add_row(str(row["score"]), str(row["text"]))
    console.print(table)


@util_app.command("agent-route")
def route_agents(
    goal: str = typer.Argument(..., help="Goal whose selected roles should be routed."),
    models: str = typer.Option(..., "--models", help="Comma-separated provider:model[:quality:cost:latency] candidates."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    max_agents: int = typer.Option(8, "--max-agents", min=1, max=32, help="Maximum selected roles."),
    use_scorecards: bool = typer.Option(False, "--use-scorecards", help="Use stored local evaluation quality for matching model specs."),
) -> None:
    """Recommend a provider/model per role from explicit evidence and local health."""
    from .agent_observability import provider_observations
    from .agent_routing import parse_candidates, roster_from_decisions, route_profiles
    from .model_scorecards import ModelScorecardStore, apply_scorecards
    from .orchestrate import select_agents_for_goal
    from .provider_health_store import ProviderHealthStore

    try:
        candidates = parse_candidates(models)
        if use_scorecards:
            candidates = apply_scorecards(candidates, ModelScorecardStore(root).list())
        health = provider_observations(root)
        health.update(ProviderHealthStore(root).view())
        decisions = route_profiles(
            select_agents_for_goal(goal, root, max_agents=max_agents),
            candidates, health,
        )
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Role")
    table.add_column("Model")
    table.add_column("Score")
    table.add_column("Reason")
    for decision in decisions:
        table.add_row(decision.role, decision.spec, str(decision.score), decision.reason)
    console.print(table)
    console.print(f"[dim]Suggested roster:[/dim] {roster_from_decisions(decisions)}")
    console.print("[dim]Use it with `ronin util orchestrate --roster ...`; routing never changes a run implicitly.[/dim]")


scorecards_app = typer.Typer(help="Project-local model evaluation evidence for routing decisions.", no_args_is_help=True)
util_app.add_typer(scorecards_app, name="scorecards")


@scorecards_app.command("import")
def scorecards_import(
    report: Path = typer.Argument(..., exists=True, readable=True, help="SWE-bench or judge-report JSON."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    model: Optional[str] = typer.Option(None, "--model", help="Override a missing report model label."),
) -> None:
    """Import a model score from a generated evaluation report."""
    from .model_scorecards import ModelScorecardStore

    try:
        card = ModelScorecardStore(root).import_report(report, model=model)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"[green]saved[/green] {card.model}: {card.quality:.1%} from {card.source}")


@scorecards_app.command("show")
def scorecards_show(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Show the local benchmark and evaluation evidence available to routing."""
    from .model_scorecards import ModelScorecardStore

    cards = ModelScorecardStore(root).list()
    if not cards:
        console.print("[dim]no model scorecards. Run `ronin util bench` or import an evaluation report.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Model")
    table.add_column("Quality", justify="right")
    table.add_column("Cases", justify="right")
    table.add_column("Source")
    table.add_column("Latency", justify="right")
    for card in sorted(cards.values(), key=lambda item: (-item.quality, item.model)):
        latency = "-" if card.latency_seconds is None else f"{card.latency_seconds:.2f}s"
        table.add_row(card.model, f"{card.quality:.1%}", str(card.cases), card.source, latency)
    console.print(table)


proposals_app = typer.Typer(
    help="Review and explicitly stage verified isolated-worktree proposals.", no_args_is_help=True,
)
util_app.add_typer(proposals_app, name="proposals")


@proposals_app.command("list")
def proposals_list(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """List local patches retained from write orchestrations."""
    from .agent_proposals import AgentProposalStore

    proposals = AgentProposalStore(root).list()
    if not proposals:
        console.print("[dim]no isolated-worktree proposals recorded yet.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Run", no_wrap=True)
    table.add_column("Status")
    table.add_column("Roles")
    table.add_column("Base")
    for proposal in proposals:
        table.add_row(
            proposal.run_id, proposal.status, ", ".join(patch.role for patch in proposal.patches),
            proposal.base_revision[:12],
        )
    console.print(table)


@proposals_app.command("show")
def proposals_show(
    run_id: str = typer.Argument(..., help="Agent run id owning the proposal."),
    role: str = typer.Option(..., "--role", help="Role patch to display."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Print one retained role patch without modifying the repository."""
    from .agent_proposals import AgentProposalStore

    try:
        patch = AgentProposalStore(root).read_patch(run_id, role)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(patch, end="" if patch.endswith("\n") else "\n")


@proposals_app.command("apply")
def proposals_apply(
    run_id: str = typer.Argument(..., help="Agent run id owning the proposal."),
    role: List[str] | None = typer.Option(None, "--role", help="Apply only this role; repeat to select several."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    yes: bool = typer.Option(False, "--yes", help="Explicitly stage a clean, verified proposal; never commits."),
) -> None:
    """Stage a verified proposal only after exact-revision and clean-tree checks."""
    if not yes:
        console.print("[yellow]proposal not staged[/yellow] (review it, then rerun with --yes)")
        raise typer.Exit(2)
    from .agent_proposals import AgentProposalStore

    try:
        applied = AgentProposalStore(root).apply(run_id, roles=role)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    console.print(
        f"[green]staged[/green] {applied.run_id}: {', '.join(applied.roles)} "
        "(review with `git diff --cached`; no commit was created)"
    )


trials_app = typer.Typer(help="Run competing isolated teams and choose a verified candidate only.", no_args_is_help=True)
util_app.add_typer(trials_app, name="trials")


@trials_app.command("run")
def trials_run(
    goal: str = typer.Argument(..., help="Goal every competing team receives."),
    candidate: List[str] = typer.Option(..., "--candidate", help="Repeat NAME=role=provider,... for each team."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    write: bool = typer.Option(False, "--write", help="Let candidates edit only in their own worktrees."),
    max_parallel: int = typer.Option(2, "--max-parallel", min=1, max=8, help="Maximum candidate teams running together."),
    offline: bool = typer.Option(False, "--offline", help="Use a local provider only."),
) -> None:
    """Execute explicit competing teams and rank only verified, finding-free outcomes."""
    from .agent_workflow import workflow_for_goal
    from .orchestrate import run_orchestrate
    from .worktree_trials import TrialCandidate, choose_trial, run_trials

    candidates = []
    for raw in candidate:
        name, separator, roster = raw.partition("=")
        if not separator or not name.strip() or not roster.strip():
            console.print("[red]x[/red] candidates must be NAME=role=provider,...")
            raise typer.Exit(2)
        candidates.append(TrialCandidate(name.strip(), roster.strip()))
    if len(candidates) < 2:
        console.print("[red]x[/red] trials need at least two explicit candidates")
        raise typer.Exit(2)
    config = load_config()
    if offline:
        from .offline import apply_offline
        config = apply_offline(config.model_copy(update={"offline": True}))
    elif not config.has_provider_auth():
        console.print(f"[red]x[/red] no credentials for {config.provider}")
        raise typer.Exit(2)

    def runner(item):
        return run_orchestrate(
            config, goal, roster_spec=item.roster, root=root, read_only=not write,
            workflow=workflow_for_goal(goal, write=True) if write else None,
        )

    try:
        results = run_trials(candidates, runner, max_parallel=max_parallel)
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    decision = choose_trial(results)
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Candidate")
    table.add_column("Success")
    table.add_column("Verified")
    table.add_column("Quality")
    for result in results:
        table.add_row(result.name, str(result.success), str(result.verifier_passed), str(round(result.quality, 3)))
    console.print(table)
    if decision.winner is None:
        console.print("[red]no trial met the acceptance rule; no diff was applied.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]selected[/green] {decision.winner.name}; inspect its stored run and diff before applying anything.")


@util_app.command("agent-runs")
def agent_runs(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Maximum recent runs to show."),
) -> None:
    """Show a local terminal dashboard of agent work, isolation, and providers."""
    from .agent_observability import dashboard_counts, list_agent_runs, provider_observations

    runs = list_agent_runs(root, limit=limit)
    counts = dashboard_counts(root)
    console.print("[bold]agent runs[/bold] " + " ".join(f"{status}={count}" for status, count in sorted(counts.items())))
    if not runs:
        console.print("[dim]no project-local agent task state yet.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Run", no_wrap=True)
    table.add_column("Status")
    table.add_column("Agents", justify="right")
    table.add_column("Subtasks", justify="right")
    table.add_column("Isolation")
    table.add_column("Goal", overflow="fold")
    for run in runs:
        table.add_row(run.id, run.status, str(run.agents), str(run.subtasks), run.isolation, run.goal)
    console.print(table)
    health = provider_observations(root)
    if health:
        console.print("\n[bold]provider observations[/bold]")
        providers = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        providers.add_column("Provider")
        providers.add_column("Status")
        providers.add_column("Attempts", justify="right")
        providers.add_column("Failures", justify="right")
        providers.add_column("Roles")
        for provider, observation in sorted(health.items()):
            providers.add_row(
                provider, str(observation["status"]), str(observation["attempts"]),
                str(observation["failures"]), ", ".join(observation["roles"]),
            )
        console.print(providers)


@util_app.command("agent-recover")
def agent_recover(
    run_id: str = typer.Argument(..., help="Interrupted or failed project-local agent run id."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    mode: str = typer.Option("original", "--mode", help="original, read, or write."),
    offline: bool = typer.Option(False, "--offline", help="Run the replacement task on a local provider only."),
) -> None:
    """Start a linked replacement run with a status-only recovery handoff."""
    from .agent_recovery import load_recovery_context, recover_profiles
    from .agent_workflow import workflow_for_goal
    from .orchestrate import agent_catalog, run_orchestrate

    try:
        context = load_recovery_context(root, run_id)
        profiles = recover_profiles(root, context, agent_catalog(root, manifest=context.agent_manifest))
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    chosen = mode.strip().lower()
    if chosen not in {"original", "read", "write"}:
        console.print("[red]x[/red] --mode must be original, read, or write")
        raise typer.Exit(2)
    write = context.write if chosen == "original" else chosen == "write"
    config = load_config()
    if offline:
        from .offline import apply_offline
        config = apply_offline(config.model_copy(update={"offline": True}))
    elif not config.has_provider_auth():
        console.print(f"[red]x[/red] no credentials for {config.provider}; pass --offline for a local recovery")
        raise typer.Exit(2)
    console.print(f"[dim]recovering {context.run_id}: {len(context.failed)} failed, {len(context.unfinished)} unfinished subtask(s)[/dim]")
    with console.status("[dim]replanning recovery run...[/dim]", spinner="dots"):
        outcome = run_orchestrate(
            config, context.goal, roster_spec=context.roster_spec, root=root,
            read_only=not write, profiles=profiles, max_agents=max(context.max_agents, len(profiles)),
            workflow=workflow_for_goal(context.goal, write=True) if write else None,
            resume_from=context.run_id, resume_summary=context.planner_context(),
        )
    if not outcome.success:
        console.print(f"[red]x[/red] recovery failed: {outcome.error or 'no plan'}")
        raise typer.Exit(1)
    console.print(f"[green]recovered[/green] as {outcome.run_id or 'unpersisted run'}")
    console.print(outcome.output)


@util_app.command("provider-health")
def provider_health(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Show durable, observation-only health and cooldown state for providers."""
    from .provider_health_store import ProviderHealthStore

    rows = ProviderHealthStore(root).view()
    if not rows:
        console.print("[dim]no provider outcomes recorded yet.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Provider")
    table.add_column("Status")
    table.add_column("Attempts", justify="right")
    table.add_column("Failures", justify="right")
    table.add_column("Roles")
    for label, row in sorted(rows.items()):
        table.add_row(label, str(row["status"]), str(row["attempts"]), str(row["failures"]), ", ".join(row["roles"]))
    console.print(table)


@util_app.command("agent-ops")
def agent_operations(
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """Show local agent operations: queue, recovery, provider health, ledger, and eval evidence."""
    from .agent_operations import operations_snapshot

    snapshot = operations_snapshot(root)
    summary = Table(box=box.SIMPLE, show_header=False)
    summary.add_column("Surface", style="cyan")
    summary.add_column("State")
    summary.add_row("runs", ", ".join(f"{key}={value}" for key, value in sorted(snapshot["runs"].items())) or "none")
    summary.add_row("queue", ", ".join(f"{key}={value}" for key, value in sorted(snapshot["queue"].items())) or "empty")
    summary.add_row("recoverable", str(len(snapshot["recoverable"])))
    summary.add_row("ledger", f"{'valid' if snapshot['ledger'].valid else 'INVALID'} ({snapshot['ledger'].events} events)")
    summary.add_row("scorecards", str(len(snapshot["scorecards"])))
    proposals = snapshot["proposals"]
    summary.add_row("proposals", f"{len(proposals)} ({sum(proposal.status == 'ready' for proposal in proposals)} ready)")
    summary.add_row("fleet plans", str(len(snapshot["fleet_plans"])))
    summary.add_row("missions", str(len(snapshot["missions"])))
    candidates = snapshot["candidate_workspaces"]
    summary.add_row("candidate workspaces", f"{len(candidates)} ({sum(item.status == 'active' for item in candidates)} active)")
    console.print(summary)
    if snapshot["providers"]:
        console.print("\n[bold]provider health[/bold]")
        for label, row in sorted(snapshot["providers"].items()):
            console.print(f"  {label}: {row['status']} ({row['attempts']} attempt(s), {row['failures']} failure(s))")


@util_app.command("sandbox")
def sandbox_status() -> None:
    """Inspect the requested command sandbox without executing a command."""
    from .backends import requested_backend
    from .sandbox_policy import inspect_sandbox_policy

    status = inspect_sandbox_policy(requested_backend())
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("field", style="cyan", no_wrap=True)
    table.add_column("value")
    table.add_row("requested", status.requested or "local")
    table.add_row("mode", status.mode)
    table.add_row("status", status.status)
    table.add_row("fail closed", "yes" if status.fail_closed else "not requested")
    table.add_row("detail", status.detail)
    console.print(table)

@util_app.command()
def orchestrate(
    goal: str = typer.Argument(..., help="The high-level goal to decompose and work."),
    roster: str = typer.Option(
        None, "--roster", "-r",
        help="Role→provider, e.g. 'researcher=anthropic,implementer=cerebras,"
             "reviewer=gemini,tester=groq'. Core and selected specialist profiles "
             "not listed run on the base provider."),
    write: bool = typer.Option(
        False, "--write",
        help="Let implementer sub-agents edit code in isolated git worktrees (needs git). "
             "Default is read-only (research/plan)."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
    max_agents: int = typer.Option(
        8, "--max-agents", help="Maximum task-relevant specialist profiles (4-32).",
    ),
    max_parallel: int = typer.Option(
        4, "--max-parallel", help="Maximum independent sub-agents running at once.",
    ),
    max_subtask_iterations: int = typer.Option(
        12, "--max-subtask-iterations", help="Maximum ReAct turns reserved for each subtask.",
    ),
    max_subtask_tokens: Optional[int] = typer.Option(
        None, "--max-subtask-tokens", help="Optional reported-token ceiling for each sub-agent.",
    ),
    max_total_subtask_iterations: Optional[int] = typer.Option(
        None, "--max-total-subtask-iterations",
        help="Global reservation ceiling across planned subtasks (default: team size x per-subtask limit).",
    ),
    agent_timeout: Optional[float] = typer.Option(
        300.0, "--agent-timeout", help="Wall-clock seconds allowed for each sub-agent.",
    ),
    agent_manifest: Optional[Path] = typer.Option(
        None, "--agent-manifest", help="Project profile manifest (default: .ronin/agents.json).",
    ),
    offline: bool = typer.Option(
        False, "--offline", help="Run on a local brain only; no network, no API keys."),
) -> None:
    """🧭 Decompose a goal into subtasks, assign each to a specialist sub-agent, run
    them (in parallel where independent), and synthesize the results.

    The differentiator is provider-agnostic sub-agents: each role can run on a
    DIFFERENT vendor's model via --roster, while the planner and synthesizer run
    on your base provider. Complements `swarm` (a fixed architect→implementer→
    reviewer pipeline) by letting the planner choose the subtasks and their
    dependencies dynamically.

    Core roles are researcher (read-only investigation), implementer (edits code),
    reviewer (critiques a change), and tester (writes and runs tests). Ronin adds
    task-matched profiles from its specialist catalog up to --max-agents.

    Example:  ronin orchestrate "add retry + tests to the http client" \\
              -r researcher=anthropic,implementer=cerebras,reviewer=gemini,tester=groq --write
    """
    from .agent_governance import OrchestrationGovernance
    from .agent_workflow import workflow_for_goal
    from .orchestrate import role_label, run_orchestrate, select_agents_for_goal

    config = load_config()
    if offline:
        from .offline import apply_offline
        config = apply_offline(config.model_copy(update={"offline": True}))
        console.print("[dim]🔒 offline — local brain, nothing leaves this machine[/dim]")
    elif not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold]. "
                      "Run [cyan]ronin init[/cyan] or pass [cyan]--offline[/cyan].")
        raise typer.Exit(2)

    parsed = {}
    if roster:
        from .orchestrate import parse_roster
        parsed = parse_roster(roster)
    try:
        selected = select_agents_for_goal(
            goal, root, max_agents=max_agents, manifest=agent_manifest,
        )
        governance = OrchestrationGovernance(
            max_agents=max_agents,
            max_parallel=max_parallel,
            max_iterations_per_agent=max_subtask_iterations,
            max_total_subtask_iterations=(
                max_total_subtask_iterations
                if max_total_subtask_iterations is not None
                else max_agents * max_subtask_iterations
            ),
            max_wall_time_seconds_per_agent=agent_timeout,
            max_tokens_per_agent=max_subtask_tokens,
        )
    except ValueError as exc:
        console.print(f"[red]x[/red] {exc}")
        raise typer.Exit(2)
    line = " · ".join(
        f"{profile.key}: [bold]{role_label(config, parsed.get(profile.key))}[/bold]"
        for profile in selected
    )
    console.print(f"[#7aa2f7]🧭 orchestrate[/#7aa2f7] {line}")
    if write:
        console.print("[dim]implementation is worktree-isolated and requires review + test acceptance[/dim]")

    def _on_subtask_start(st, label) -> None:
        console.print(f"  [#bb9af7]◆[/#bb9af7] [bold]{st.id}[/bold] → [dim]{label}[/dim]: {st.description}")

    with console.status("[dim]planning + running sub-agents…[/dim]", spinner="dots"):
        outcome = run_orchestrate(
            config, goal, roster_spec=roster, root=root,
            on_subtask_start=_on_subtask_start, read_only=not write,
            profiles=selected, max_agents=max_agents, agent_manifest=agent_manifest,
            workflow=workflow_for_goal(goal, write=True) if write else None,
            governance=governance,
        )

    console.print()
    if not outcome.plan_subtasks:
        console.print(f"[red]✗ orchestration failed:[/red] {outcome.error or 'no plan'}")
        raise typer.Exit(1)
    for r in outcome.subtask_results:
        mark = "[green]✓[/green]" if r["success"] else "[red]✗[/red]"
        console.print(f"{mark} [bold]{r['subtask_id']}[/bold] [dim]({r['assignee']})[/dim]")

    # Archive the run so `ronin ui` can render its planner -> sub-agent tree and
    # the faithfulness of the synthesized answer. Best-effort: a write failure
    # must never sink the run the user just paid for.
    try:
        from .orchestrate import (
            roster_labels,
            score_final_faithfulness,
        )
        from .run_store import record_from_outcome, save_run

        final_faith = score_final_faithfulness(outcome)
        record = record_from_outcome(
            goal,
            outcome,
            planner_label=role_label(config, None),
            roster=roster_labels(config, roster, outcome.agent_profiles),
        )
        record["faithfulness"] = final_faith
        record["workflow"] = outcome.workflow
        record["governance"] = outcome.governance
        record["provider_health"] = outcome.provider_health
        record["agent_task_run_id"] = outcome.run_id
        save_run(record)
    except Exception:  # noqa: BLE001 — archiving is never allowed to break a run
        pass
    console.print()
    from rich.markdown import Markdown
    console.print(Markdown(outcome.output or "_(no output)_"))
    if write and outcome.worktree_diffs:
        from .kaizen import _print_diff
        for role, diff in outcome.worktree_diffs.items():
            console.print(f"\n[bold]diff ({role} isolated worktree — review before applying)[/bold]:")
            _print_diff(console, diff)
        if outcome.proposal:
            console.print(
                f"[dim]retained proposal: {outcome.proposal['run_id']} — review with "
                "`ronin util proposals show <run> --role <role>`; staging still needs --yes[/dim]"
            )
    if not outcome.success:
        raise typer.Exit(1)


# ---------- ui (web dashboard) ----------

@util_app.command()
def ui(
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind (localhost by default)."),
    port: int = typer.Option(8765, "--port", "-p", help="Port to serve the dashboard on."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the dashboard in your browser."),
) -> None:
    """🖥  Serve the ronin web dashboard on localhost.

    A single self-contained page (no external resources, works offline) that
    shows your recent runs, an expandable orchestrator sub-agent tree (planner →
    specialists by role/provider), faithfulness score badges on answers, your
    memory, and your skills. Read-only: it surfaces the REAL data ronin already
    wrote under .ronin/ — nothing leaves your machine.

    Run `ronin orchestrate "..."` first to populate the run tree, then open the
    dashboard with `ronin ui`.
    """
    try:
        import uvicorn

        from csk_api.main import app as dashboard_app  # noqa: F401
    except ImportError as exc:
        console.print(
            "[red]✗[/red] the dashboard needs the api app + uvicorn. Install the "
            "workspace dev deps:\n  [cyan]uv sync --all-packages[/cyan]\n"
            f"[dim]({exc})[/dim]"
        )
        raise typer.Exit(2)

    url = f"http://{host}:{port}/"
    console.print(f"[#7aa2f7]🖥  ronin ui[/#7aa2f7] serving the dashboard at [bold]{url}[/bold]")
    console.print("[dim]read-only · offline · ctrl-c to stop[/dim]")
    if open_browser:
        try:
            import threading
            import webbrowser

            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        except Exception:  # noqa: BLE001 — opening a browser must never block serving
            pass
    uvicorn.run(dashboard_app, host=host, port=port, log_level="warning")


# ---------- nightshift (autonomous backlog) ----------

@util_app.command()
def nightshift(
    goal: str = typer.Argument(None, help="An explicit task to work. Omit to use TODOs/issues."),
    todos: bool = typer.Option(True, "--todos/--no-todos", help="Include FIXME/TODO markers."),
    issues: bool = typer.Option(False, "--issues", help="Include open GitHub issues (needs gh)."),
    execute: bool = typer.Option(False, "--execute", help="Actually work the queue (default: dry-run plan)."),
    duel: str = typer.Option(None, "--duel", help="Red-team each patch with a rival provider[:model]."),
    swarm_roster: str = typer.Option(None, "--swarm", help="Work each task with a swarm (role=provider roster)."),
    budget: float = typer.Option(None, "--budget", help="USD spend cap for the run."),
    limit: int = typer.Option(10, "--limit", help="Max tasks."),
    parallel: int = typer.Option(1, "--parallel", help="Work N tasks concurrently (forced to 1 with --budget)."),
    redo: bool = typer.Option(False, "--redo", help="Re-attempt tasks already shipped in past runs."),
    notify: str = typer.Option(None, "--notify", help="Webhook URL to ping with the morning report (Slack/Discord/…)."),
    schedule: str = typer.Option(None, "--schedule", help="Install a cron entry, e.g. '0 2 * * *' (runs nightly)."),
    unschedule: bool = typer.Option(False, "--unschedule", help="Remove ronin's scheduled nightshift."),
    root: Path = typer.Option(Path("."), "--root", help="Repo to work in."),
) -> None:
    """🌙 Autonomous teammate — works your backlog unattended in isolated worktrees
    and leaves reviewable, test-passing patches under .ronin/nightshift/.

    Dry-run by default (shows the queue, no edits). `--execute` does the real loop:
    each task is implemented in a throwaway worktree, proven against the test suite
    (discarded if red), optionally Duel-reviewed, and saved as a patch. Never pushes,
    never touches your working tree. `ronin nightshift report` shows the results.
    """
    config = load_config()
    from .nightshift import gather_tasks, patch_path, run_nightshift, summarize

    # Scheduling: install/remove a cron entry that re-runs this nightshift nightly.
    if unschedule:
        from .cron_install import uninstall
        console.print("[green]✓ scheduled nightshift removed[/green]" if uninstall()
                      else "[dim]no scheduled nightshift to remove.[/dim]")
        return
    if schedule:
        from .cron_install import install
        import shlex
        cmd_parts = [f"cd {shlex.quote(str(root.resolve()))} &&", "ronin", "nightshift", "--execute"]
        if goal:
            cmd_parts.insert(3, shlex.quote(goal))
        if issues:
            cmd_parts.append("--issues")
        if not todos:
            cmd_parts.append("--no-todos")
        if duel:
            cmd_parts += ["--duel", shlex.quote(duel)]
        if budget:
            cmd_parts += ["--budget", str(budget)]
        if swarm_roster:
            cmd_parts += ["--swarm", shlex.quote(swarm_roster)]
        if notify:
            cmd_parts += ["--notify", shlex.quote(notify)]
        command = " ".join(cmd_parts)
        if install(schedule, command):
            console.print(f"[green]✓ scheduled[/green] [dim]({schedule})[/dim] → ronin will work this "
                          "backlog on that cron. [dim]Remove with --unschedule.[/dim]")
        else:
            console.print("[yellow]couldn't write the crontab[/yellow] [dim](is `crontab` available?)[/dim]")
        return

    tasks = gather_tasks(root, include_todos=todos, include_issues=issues, goal=goal,
                         limit=limit, skip_done=not redo)
    if not tasks:
        console.print("[dim]empty backlog — no goal, no TODO/FIXME markers"
                      f"{', no open issues' if issues else ''} (or all already shipped; --redo to re-attempt).[/dim]")
        return

    console.print(f"[#7aa2f7]🌙 nightshift queue — {len(tasks)} task(s)[/#7aa2f7]"
                  f"{' [dim](dry-run)[/dim]' if not execute else ''}")
    for i, t in enumerate(tasks, 1):
        ref = f" [dim]{t.ref}[/dim]" if t.ref else ""
        console.print(f"  [cyan]{i:>2}[/cyan] [{t.source}] [bold]{t.title}[/bold]{ref}")

    if not execute:
        console.print("\n[dim]dry-run — nothing changed. Add [bold]--execute[/bold] to work the queue.[/dim]")
        return
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)

    duel_spec = None
    if duel:
        from .consensus import parse_model_spec
        duel_spec = parse_model_spec(duel)
    _roster = None
    if swarm_roster is not None:
        from .swarm import parse_roster
        _roster = parse_roster(swarm_roster)
        console.print("[dim]each task worked by a swarm (architect → implementer → reviewer).[/dim]")
    console.print("[dim]working the queue (isolated worktrees, test-gated)…[/dim]\n")
    results = run_nightshift(config, root, console, tasks, execute=True,
                             duel_against=duel_spec, budget=budget, roster=_roster,
                             parallel=parallel)
    counts = summarize(results)
    console.print(f"\n[bold]🌙 morning report[/bold] — [green]{counts['patched']} patch(es)[/green], "
                  f"{counts['failed-tests']} failed tests, {counts['no-change']} no-change, "
                  f"{counts['error']} error(s)")
    patched = [r for r in results if r.status == "patched"]
    if patched:
        console.print("[dim]review + apply:[/dim]")
        for r in patched:
            warn = f" [yellow]⚠ {len(r.blockers)} blocker(s)[/yellow]" if r.blockers else ""
            console.print(f"  [green]✓[/green] {r.task.title}{warn}\n"
                          f"     [dim]git apply {r.patch_path}[/dim]")

    if notify:
        from .notify import format_report, post_webhook
        text = format_report(counts, [r.task.title for r in patched])
        ok = post_webhook(notify, text)
        console.print("[dim]📣 morning report sent[/dim]" if ok
                      else "[yellow]📣 couldn't reach the webhook[/yellow]")


# ---------- failover (cross-provider resilience) ----------

@util_app.command()
def failover(
    providers: str = typer.Argument(None, help="Ordered fallbacks, e.g. 'groq,gemini'. Omit to show current."),
    off: bool = typer.Option(False, "--off", help="Turn failover off."),
    scope: str = typer.Option("user", "--scope", help="Where to save: 'user' or 'project'."),
) -> None:
    """🔁 Failover: when your provider rate-limits or errors, ronin instantly
    continues on the next instead of waiting out a ~60s backoff.

    On Cerebras this works out of the box with no extra key: a rate-limit on one
    model retries the same request on its sibling on the same key (zai-glm-4.7
    <-> gpt-oss-120b). Set an explicit cross-provider chain to override it.

    Example:  ronin failover groq,gemini   (Cerebras → Groq → Gemini)
    Non-last providers fail FAST (one quick retry) so a 429 hops onward.
    Run with no argument to see the active chain (including the automatic one).
    """
    from .config import save_config
    cfg = load_config()
    if off:
        cfg = cfg.model_copy(update={"failover": []})
        save_config(cfg, scope=scope)
        console.print("[green]✓ failover off[/green]")
        return
    if not providers:
        if cfg.failover:
            chain = " → ".join([cfg.provider] + [f.get("provider", "?") for f in cfg.failover])
            console.print(f"[#6b7089]failover:[/#6b7089] [bold]{chain}[/bold]")
            return
        # No explicit chain: show the automatic same-key fallback if one applies
        # (e.g. Cerebras retries the request on its sibling model on the same key).
        from .runner import default_failover_specs
        auto = default_failover_specs(cfg)
        if auto:
            chain = " → ".join(
                [f"{cfg.provider}:{cfg.resolved_model()}"]
                + [f"{cfg.provider}:{s['model']}" for s in auto]
            )
            console.print(f"[#6b7089]failover (automatic, same key):[/#6b7089] [bold]{chain}[/bold]\n"
                          "[dim]a rate-limit on one model instantly retries on the other for free.[/dim]")
        else:
            console.print("[dim]failover off — set it with [bold]ronin failover groq,gemini[/bold].[/dim]")
        return
    from .config_cmd import failover_specs
    specs = failover_specs(providers)
    cfg = cfg.model_copy(update={"failover": specs})
    path = save_config(cfg, scope=scope)
    chain = " → ".join([cfg.provider] + [s["provider"] for s in specs])
    console.print(f"[green]✓ failover on[/green] — [bold]{chain}[/bold] → [cyan]{path}[/cyan]\n"
                  "[dim]a rate-limit on one now hops to the next in ~1s instead of waiting.[/dim]")


# ---------- config (view / set settings) ----------

@app.command()
def config(
    set_: str = typer.Option(None, "--set", help="Set a setting: --set field=value (e.g. budget=0.50)."),
    scope: str = typer.Option("project", "--scope", help="Where to save: 'project' or 'user'."),
) -> None:
    """⚙ View core settings, or set one with --set field=value.

    Settable: provider · model · route_fast · route_strong · budget · sentinel · interaction_style.
    Example:  ronin config --set route_fast=cerebras:gpt-oss-120b
    """
    cfg = load_config()
    if set_:
        from .config import save_config
        from .config_cmd import BadSetting, coerce_value, parse_set
        try:
            field, raw = parse_set(set_)
            value = coerce_value(field, raw)
        except BadSetting as e:
            console.print(f"[red]✗ {e}[/red]")
            raise typer.Exit(2)
        cfg = cfg.model_copy(update={field: value})
        path = save_config(cfg, scope=scope)
        console.print(f"[green]✓ {field} = {value}[/green] → [cyan]{path}[/cyan]")
        return

    from .config_cmd import SETTABLE
    table = Table(title="ronin config", box=box.ROUNDED)
    table.add_column("setting", style="cyan")
    table.add_column("value", style="bold")
    table.add_column("", style="#6b7089")
    rows = {
        "provider": cfg.provider,
        "model": cfg.resolved_model(),
        "route_fast": cfg.route_fast or "—",
        "route_strong": cfg.route_strong or "—",
        "budget": f"${cfg.budget:.2f}" if cfg.budget else "—",
        "sentinel": "on" if cfg.sentinel else "off",
        "interaction_style": cfg.interaction_style,
        "relational_checkins": "on" if cfg.relational_checkins else "off",
    }
    for k, v in rows.items():
        table.add_row(k, str(v), SETTABLE.get(k, ("", ""))[1])
    console.print(table)
    routing = "on" if (cfg.route_fast and cfg.route_strong) else "off"
    console.print(f"[dim]Cost Router: [bold]{routing}[/bold] · set it up fast with "
                  "[bold]ronin setup[/bold].[/dim]")


# ---------- changelog (from commits) ----------

@dev_app.command()
def changelog(
    since: str = typer.Option(None, "--since", help="Ref to start from (default: last tag, else all)."),
    version: str = typer.Option("Unreleased", "--version", help="Version heading."),
    write: bool = typer.Option(False, "--write", help="Prepend the section to CHANGELOG.md."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🧾 Generate a changelog section from your commits (Conventional-Commits
    aware: feat/fix/refactor/… grouped). Prints it, or --write prepends to CHANGELOG.md.
    """
    from .changelog import render_changelog
    from .git_helper import _git

    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        console.print("[yellow]not a git repository[/yellow]")
        raise typer.Exit(2)
    if since is None:
        tag = _git(root, "describe", "--tags", "--abbrev=0").stdout.strip()
        since = tag or None
    rng = f"{since}..HEAD" if since else "HEAD"
    log = _git(root, "log", rng, "--pretty=%s", "--no-merges").stdout
    subjects = [ln for ln in log.splitlines() if ln.strip()]
    if not subjects:
        console.print(f"[dim]no commits {('since ' + since) if since else 'found'}.[/dim]")
        return
    section = render_changelog(subjects, version=version)
    if write:
        cl = Path(root) / "CHANGELOG.md"
        existing = cl.read_text(encoding="utf-8") if cl.is_file() else "# Changelog\n"
        # insert after the first heading line
        head, _, rest = existing.partition("\n")
        cl.write_text(f"{head}\n\n{section}\n{rest.lstrip()}", encoding="utf-8")
        console.print(f"[green]✓ prepended {len(subjects)} commit(s) →[/green] [cyan]{cl}[/cyan]")
    else:
        import sys
        sys.stdout.write(section)


# ---------- scan (secret scanning) ----------

@dev_app.command()
def scan(
    staged: bool = typer.Option(False, "--staged", help="Scan only files staged for commit."),
    history: bool = typer.Option(False, "--history", help="Scan the whole git history (git log -p), not just the working tree — catches secrets committed and later removed."),
    since: str = typer.Option("", "--since", help="With --history: only commits since this date/ref (git --since), e.g. '3 months ago'."),
    max_commits: int = typer.Option(0, "--max-commits", help="With --history: cap the number of commits walked (0 = all)."),
    quiet: bool = typer.Option(False, "--quiet", help="No output; just the exit code (for hooks)."),
    root: Path = typer.Option(Path("."), "--root", help="Directory to scan."),
) -> None:
    """🔍 Scan the repo for leaked secrets (API keys, tokens, private keys) and
    report each as [bold]file:line + kind[/bold] — the secret value is NEVER
    printed, only a masked hint. Exits non-zero if any are found, so it can also
    back a pre-commit hook ([bold]ronin hook install[/bold]).

    [bold]--history[/bold] walks every commit and flags secrets on added lines,
    catching keys that were committed and later deleted but still live in the
    git history. Cap large repos with [bold]--since[/bold] / [bold]--max-commits[/bold].
    """
    from .secret_scan import find_secrets, render_findings, scan_repo

    if history:
        from .secret_scan import scan_git_history
        hits = scan_git_history(root, max_commits=max_commits, since=since)
        if not quiet:
            if not hits:
                console.print("[green]✓ no secrets found in git history[/green]")
            else:
                console.print(f"[bold #f7768e]✗ {len(hits)} secret(s) found in git history:[/bold #f7768e]")
                for h in hits:
                    console.print(
                        f"  [red]{h['commit'][:10]}[/red] "
                        f"[cyan]{h['path']}:{h['line']}[/cyan] "
                        f"[dim]{h['kind']} ({h['hint']})[/dim]"
                    )
        if hits:
            raise typer.Exit(1)
        return

    if staged:
        # Line-level scan of just the files staged for commit. Reuse the staged-
        # file resolver from the hook helper, then run the rich per-line scanner.
        from .scan import load_ignore, is_ignored, staged_files
        root_path = root.resolve()
        ignore = load_ignore(root_path)
        findings: list[dict] = []
        for rel in staged_files(root_path):
            if ignore and is_ignored(rel, ignore):
                continue
            p = root_path / rel
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            findings.extend(find_secrets(text, rel))
        findings.sort(key=lambda f: (f["path"], f["line"]))
    else:
        findings = scan_repo(root)

    if not quiet:
        render_findings(findings, console)
    if findings:
        raise typer.Exit(1)


hook_app = typer.Typer(help="Manage ronin's git pre-commit secret-scan hook.")
util_app.add_typer(hook_app, name="hook")


# Tool-event hooks (.ronin/hooks.json) — distinct from the git pre-commit `hook`.
hooks_app = typer.Typer(help="Trust/list .ronin/hooks.json — shell commands ronin runs on tool events.")
util_app.add_typer(hooks_app, name="hooks")


@hooks_app.command("list", help="Show the hooks declared in .ronin/hooks.json and whether they're trusted.")
def hooks_list(root: Path = typer.Option(Path("."), "--root", help="Project root.")) -> None:
    from .hooks import hooks_config_path, load_hooks
    from .plugin_trust import is_trusted

    cfg = hooks_config_path(root)
    if not cfg.is_file():
        console.print("[dim]no .ronin/hooks.json here[/dim]")
        return
    trusted = is_trusted(cfg)
    hooks = load_hooks(root, require_trust=False)
    state = "[#9ece6a]trusted[/#9ece6a]" if trusted else "[#f7768e]UNTRUSTED — not run[/#f7768e]"
    console.print(f"[bold].ronin/hooks.json[/bold] ({state}) — {len(hooks)} hook(s):")
    for h in hooks:
        console.print(f"  [cyan]{h.get('event','?')}[/cyan]  {h.get('command','')}")
    if not trusted:
        console.print("[dim]review the commands above, then: ronin hooks trust[/dim]")


@hooks_app.command("trust", help="Trust .ronin/hooks.json so its hooks run (each is a shell command).")
def hooks_trust(root: Path = typer.Option(Path("."), "--root", help="Project root.")) -> None:
    """🔐 Approve this repo's tool-event hooks.

    Each hook runs a shell command when the agent edits a file or runs a command,
    so a repo-committed hooks.json is arbitrary code execution. ronin refuses to run
    hooks from a config you have not trusted. Trusting it is equivalent to agreeing
    to run the commands shown; review them first.
    """
    from .hooks import hooks_config_path, load_hooks
    from .plugin_trust import is_trusted, trust

    cfg = hooks_config_path(root)
    if not cfg.is_file():
        console.print("[dim]no .ronin/hooks.json here[/dim]")
        raise typer.Exit(1)
    if is_trusted(cfg):
        console.print("[#9ece6a]✓ already trusted.[/#9ece6a]")
        return
    hooks = load_hooks(root, require_trust=False)
    console.print(f"[bold]this will let {len(hooks)} hook(s) run shell commands on tool events:[/bold]")
    for h in hooks:
        console.print(f"  [cyan]{h.get('event','?')}[/cyan]  {h.get('command','')}")
    trust(cfg)
    console.print("[#9ece6a]✓[/#9ece6a] trusted — hooks run next time you use ronin here.")


@hooks_app.command("untrust", help="Revoke trust so .ronin/hooks.json hooks stop running.")
def hooks_untrust(root: Path = typer.Option(Path("."), "--root", help="Project root.")) -> None:
    """🔒 Revoke this repo's tool-event hooks — they will not run again."""
    from .hooks import hooks_config_path
    from .plugin_trust import untrust

    cfg = hooks_config_path(root)
    if untrust(cfg):
        console.print("[#9ece6a]✓[/#9ece6a] revoked — .ronin/hooks.json hooks will not run.")
    else:
        console.print("[dim].ronin/hooks.json was not trusted[/dim]")


@hook_app.command("install")
def hook_install(root: Path = typer.Option(Path("."), "--root", help="Repo root.")) -> None:
    """Install the pre-commit hook that blocks commits containing secrets."""
    from .git_hook import install_hook
    try:
        path, fresh = install_hook(root)
    except FileNotFoundError as e:
        console.print(f"[yellow]{e}[/yellow]")
        raise typer.Exit(2)
    console.print(f"[green]✓ hook installed[/green] → [cyan]{path}[/cyan]" if fresh
                  else "[dim]hook already installed.[/dim]")


@hook_app.command("uninstall")
def hook_uninstall(root: Path = typer.Option(Path("."), "--root", help="Repo root.")) -> None:
    """Remove ronin's pre-commit hook snippet (preserves any other hook content)."""
    from .git_hook import uninstall_hook
    console.print("[green]✓ hook removed[/green]" if uninstall_hook(root)
                  else "[dim]no ronin hook to remove.[/dim]")


# ---------- setup (first-run wizard) ----------

@util_app.command()
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


# ---------- types (add missing type hints) ----------

@dev_app.command()
def types(
    execute: bool = typer.Option(False, "--execute", help="Add type hints (default: list gaps)."),
    limit: int = typer.Option(60, "--limit", help="Max functions."),
    budget: float = typer.Option(None, "--budget", help="USD spend cap."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🏷  Find public functions missing parameter/return type hints, then (with
    --execute) add precise hints — test-gated, as reviewable patches.
    """
    from .types_gap import find_type_gaps, to_tasks

    gaps = find_type_gaps(root, limit=limit)
    if not gaps:
        console.print("[green]✓ every public function is annotated.[/green]")
        return
    by_file: dict[str, int] = {}
    for g in gaps:
        by_file[g.file] = by_file.get(g.file, 0) + 1
    console.print(f"[#7aa2f7]🏷 {len(gaps)} under-annotated function(s)[/#7aa2f7] in {len(by_file)} file(s):")
    for f, n in list(by_file.items())[:20]:
        console.print(f"  [cyan]{f}[/cyan] [dim]{n} function(s)[/dim]")
    if not execute:
        console.print("\n[dim]add [bold]--execute[/bold] to add type hints (reviewable patches).[/dim]")
        return
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .nightshift import run_nightshift, summarize
    console.print("[dim]adding type hints (isolated worktrees, test-gated)…[/dim]\n")
    results = run_nightshift(config, root, console, to_tasks(gaps), execute=True, budget=budget)
    counts = summarize(results)
    console.print(f"\n[bold]🏷 types[/bold] — [green]{counts['patched']} file(s) annotated[/green]")
    console.print("[dim]review with [bold]ronin patches[/bold].[/dim]")


# ---------- deps (dependency audit) ----------

@dev_app.command()
def deps(root: Path = typer.Option(Path("."), "--root", help="Repo root.")) -> None:
    """📦 Audit dependencies — cross-checks what your code imports against what the
    project declares: flags declared-but-unused deps and used-but-undeclared imports.
    """
    from .deps import audit

    a = audit(root)
    console.print(f"[#7aa2f7]📦 {len(a['used'])} third-party package(s) imported[/#7aa2f7]")
    if a["undeclared"]:
        console.print(f"\n[bold #f7768e]⚠ used but NOT declared[/bold #f7768e] "
                      "[dim](add to pyproject/requirements?)[/dim]")
        for d in a["undeclared"]:
            console.print(f"  [#f7768e]{d}[/#f7768e]")
    if a["unused"]:
        console.print(f"\n[bold #e0af68]· declared but not imported[/bold #e0af68] "
                      "[dim](unused, or used indirectly?)[/dim]")
        for d in a["unused"]:
            console.print(f"  [#e0af68]{d}[/#e0af68]")
    if not a["undeclared"] and not a["unused"]:
        console.print("[green]✓ declared deps and imports line up.[/green]")


# ---------- onboard (understand a new repo fast) ----------

@dev_app.command()
def onboard(
    repo: str = typer.Argument(".", help="A git URL to clone, or a local path."),
    keep: bool = typer.Option(False, "--keep", help="Keep the cloned repo (don't delete the temp dir)."),
) -> None:
    """🧭 Drop into any repo and get oriented — clones a URL (or reads a local path),
    gathers the stack, layout, entry points and architecture, then writes you an
    onboarding guide: what it is, where to start, how to build/run/test.
    """
    import shutil
    import subprocess
    import tempfile

    from .onboard_repo import gather_facts, onboard_prompt

    tmp: str | None = None
    from .onboard_repo import is_git_url
    if is_git_url(repo):
        tmp = tempfile.mkdtemp(prefix="ronin-onboard-")
        console.print(f"[#7aa2f7]🧭 cloning[/#7aa2f7] [bold]{repo}[/bold] [dim]→ shallow[/dim]")
        try:
            r = subprocess.run(["git", "clone", "--depth", "1", repo, tmp],
                               capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as e:
            console.print(f"[red]clone failed: {e}[/red]")
            raise typer.Exit(1)
        if r.returncode != 0:
            console.print(f"[red]clone failed:[/red] {r.stderr.strip()[:300]}")
            shutil.rmtree(tmp, ignore_errors=True)
            raise typer.Exit(1)
        root = Path(tmp)
    else:
        root = Path(repo)
        if not root.is_dir():
            console.print(f"[red]not a directory:[/red] {repo}")
            raise typer.Exit(1)

    try:
        console.print("[#6b7089]gathering facts…[/#6b7089]")
        facts = gather_facts(root)
        stack = ", ".join(facts.get("stack", [])) or "unknown"
        console.print(f"  stack [bold]{stack}[/bold]"
                      + (f"  ·  [bold]{facts['modules']}[/bold] modules in [cyan]{facts['package']}[/cyan]"
                         if facts.get("package") else ""))
        if facts.get("entry_points"):
            console.print(f"  entry points  [cyan]{', '.join(facts['entry_points'])}[/cyan]")

        config = load_config()
        if not config.has_provider_auth():
            console.print("[dim]set a provider to generate the full onboarding guide.[/dim]")
            return
        from .code_mode import run_code_agent
        console.print("\n[#6b7089]writing your onboarding guide…[/#6b7089]\n")
        run_code_agent(config, onboard_prompt(facts), root=root, console=console,
                       read_only=True, include_image_tool=False, max_iterations=14)
    finally:
        if tmp and not keep:
            shutil.rmtree(tmp, ignore_errors=True)
        elif tmp and keep:
            console.print(f"\n[dim]cloned repo kept at[/dim] {tmp}")


# ---------- usages (deep-dive one symbol + its call sites) ----------

@dev_app.command()
def usages(
    symbol: str = typer.Argument(..., help="A function/class/method name to deep-dive."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Just show where it's defined + used."),
) -> None:
    """🔍 Deep-dive a symbol — find where it's defined and every place it's used,
    then explain its contract, behaviour, and how callers depend on it.
    """
    from .symbol_lens import explain_prompt, locate

    loc = locate(root, symbol)
    if not loc["definitions"] and loc["total_sites"] == 0:
        console.print(f"[yellow]no definition or usage of[/yellow] [bold]{symbol}[/bold] [dim]found.[/dim]")
        raise typer.Exit(1)

    for d in loc["definitions"]:
        console.print(f"[#7aa2f7]🔍 {d.kind}[/#7aa2f7] [bold]{symbol}[/bold] "
                      f"[dim]→[/dim] [cyan]{d.file}:{d.line}[/cyan]")
    if not loc["definitions"]:
        console.print(f"[#e0af68]🔍 {symbol}[/#e0af68] [dim]used but not defined here (imported?)[/dim]")
    console.print(f"[#6b7089]{loc['total_sites']} call site(s)[/#6b7089]"
                  + ("[dim] — showing first few:[/dim]" if loc["call_sites"] else ""))
    for s in loc["call_sites"][:8]:
        console.print(f"  [dim]{s.file}:{s.line}[/dim]  {s.text[:80]}")

    if no_ai:
        return
    config = load_config()
    if not config.has_provider_auth():
        console.print("[dim]set a provider for the full explanation.[/dim]")
        return
    from .code_mode import run_code_agent
    console.print("\n[#6b7089]reading the code…[/#6b7089]\n")
    run_code_agent(config, explain_prompt(loc, symbol), root=root, console=console,
                   read_only=True, include_image_tool=False, max_iterations=12)


# ---------- browse (optional web computer-use via Playwright) ----------

@util_app.command()
def browse(
    task: str = typer.Argument(..., help="What to do on the web, e.g. 'open example.com and read it'."),
    root: Path = typer.Option(Path("."), "--root", help="Project root (for screenshots)."),
) -> None:
    """Drive a real web browser to do a task — free, no vision model needed.

    Optional extra. Reads pages from the DOM / accessibility tree, so it works
    without a vision model. Enable it once with:
      pip install 'ronin-cli[browser]' && playwright install chromium
    """
    from .browser_tools import browser_tools_available, install_hint

    if not browser_tools_available():
        console.print("[yellow]browser tools not installed.[/yellow]")
        console.print(f"[dim]{install_hint()}[/dim]")
        raise typer.Exit(2)

    config = load_config()
    if not config.has_provider_auth():
        console.print("[yellow]browse needs a provider[/yellow] — run [bold]ronin login[/bold].")
        raise typer.Exit(2)

    from .browser_tools import browse_close
    from .code_mode import run_code_agent

    console.print("\n[#6b7089]driving the browser…[/#6b7089]\n")
    try:
        run_code_agent(config, task, root=root, console=console,
                       include_image_tool=False, max_iterations=20)
    finally:
        browse_close()  # always release the browser


# ---------- watch (re-run on save) ----------

@dev_app.command()
def watch(
    command: list[str] = typer.Argument(None, help="Command to run on change. Default: pytest -q."),
    root: Path = typer.Option(Path("."), "--root", help="Directory to watch."),
    interval: float = typer.Option(1.0, "--interval", help="Poll interval in seconds."),
    fix: bool = typer.Option(False, "--fix", help="On failure, hand the output to ronin to fix it."),
) -> None:
    """👀 Re-run a command whenever your code changes — your tests by default.
    With --fix, a failing run is handed straight to ronin to repair (then watching
    resumes). Ctrl-C to stop.
    """
    import subprocess
    import time

    from .watch import changed, describe_change, snapshot

    cmd = list(command) if command else ["pytest", "-q"]
    cmd_str = " ".join(cmd)
    console.print(f"[#7aa2f7]👀 watching[/#7aa2f7] [bold]{root}[/bold] "
                  f"[dim]→ runs[/dim] [cyan]{cmd_str}[/cyan] [dim]on save · Ctrl-C to stop[/dim]")

    def _run() -> int:
        console.print(f"\n[#6b7089]── running[/#6b7089] [cyan]{cmd_str}[/cyan] [dim]──[/dim]")
        try:
            r = subprocess.run(cmd, cwd=str(root))
            rc = r.returncode
        except (OSError, subprocess.SubprocessError) as e:
            console.print(f"[red]couldn't run: {e}[/red]")
            return 1
        if rc == 0:
            console.print("[green]✓ passed[/green]")
        else:
            console.print(f"[#f7768e]✗ exit {rc}[/#f7768e]")
            if fix:
                config = load_config()
                if config.has_provider_auth():
                    from .code_mode import run_code_agent
                    console.print("[#6b7089]handing the failure to ronin…[/#6b7089]")
                    run_code_agent(
                        config,
                        f"The command `{cmd_str}` is failing. Run it, read the output, find "
                        "the cause, and fix it. Keep changes minimal and re-run to confirm green.",
                        root=root, console=console, max_iterations=20)
        return rc

    _run()
    prev = snapshot(root)
    try:
        while True:
            time.sleep(max(0.2, interval))
            cur = snapshot(root)
            hits = changed(prev, cur)
            if hits:
                prev = cur
                console.print(f"\n[#e0af68]changed:[/#e0af68] [dim]{describe_change(hits, root)}[/dim]")
                _run()
                prev = snapshot(root)              # re-snapshot (a --fix run may edit files)
    except KeyboardInterrupt:
        console.print("\n[dim]👋 stopped watching.[/dim]")


# ---------- verify (detect + run the repo's tests; --fix repairs to green) ----------

@dev_app.command()
def verify(
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
    fix: bool = typer.Option(False, "--fix", help="If red, hand the failure to ronin and repair to green."),
    rounds: int = typer.Option(3, "--rounds", help="Max repair attempts with --fix."),
) -> None:
    """✅ Detect and run this repo's test command (pytest, npm, cargo, go, make,
    or a `verify:` line in RONIN.md) and report pass/fail. With --fix, a failing
    run is handed to ronin, which fixes and re-runs until green (or --rounds).
    """
    from .verify_cmd import detect_verify_command, run_verify

    detected = detect_verify_command(root)
    if detected is None:
        console.print("[yellow]no test command detected[/yellow] — add a "
                      "[bold]verify: <command>[/bold] line to RONIN.md to set one.")
        raise typer.Exit(2)
    cmd, runner = detected
    console.print(f"[#7aa2f7]✅ verify[/#7aa2f7] [dim]({runner})[/dim] [cyan]{' '.join(cmd)}[/cyan]")

    if not fix:
        v = run_verify(root)
        if v.passed:
            console.print(f"[green]✓ green[/green] [dim]{v.summary}[/dim]")
            raise typer.Exit(0)
        console.print(f"[#f7768e]✗ red[/#f7768e] [dim]{v.summary}[/dim]")
        raise typer.Exit(1)

    # --fix: repair to green
    config = load_config()
    if not config.has_provider_auth():
        console.print("[yellow]--fix needs a provider key[/yellow] — run [bold]ronin login[/bold].")
        raise typer.Exit(2)
    from .code_mode import run_code_agent
    from .repair import repair_to_green

    # --fix auto-approves edits (yolo) on the real tree, so snapshot first — a
    # repair that makes things worse can be undone with `ronin rewind` / the
    # checkpoint tools. (Best-effort: a non-git tree just skips the snapshot.)
    try:
        from .checkpoint import create_checkpoint
        cp = create_checkpoint(root, label="before ronin verify --fix")
        console.print(f"[dim]📌 checkpoint #{cp.id} taken — `ronin rewind` to undo[/dim]")
    except Exception:  # noqa: BLE001 — never block the fix on snapshot failure
        pass

    def _run_agent(prompt: str) -> None:
        run_code_agent(config, prompt, root=root, console=console, yolo=True, max_iterations=25)

    def _on_round(attempt: int, v) -> None:
        console.print(f"[#6b7089]↻ repair {attempt}/{rounds} — handing the failure "
                      f"to ronin…[/#6b7089] [dim]{v.summary}[/dim]")

    final = repair_to_green(root, _run_agent, rounds=rounds, on_round=_on_round)
    if final.passed:
        console.print(f"[green]✓ green[/green] [dim]{final.summary}[/dim]")
        raise typer.Exit(0)
    console.print(f"[#f7768e]✗ still red after {rounds} attempt(s)[/#f7768e] "
                  f"[dim]{final.summary}[/dim]")
    raise typer.Exit(1)


# ---------- auto (supervised autonomy: checkpoint + test-gate + stall-stop) ----------

@dev_app.command()
def auto(
    task: list[str] = typer.Argument(..., help="The task to work, in plain words."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
    steps: int = typer.Option(6, "--steps", help="Hard ceiling on agent steps."),
    max_failures: int = typer.Option(
        2, "--max-failures", help="Stop after this many failing test gates in a row."),
    stall_after: int = typer.Option(
        2, "--stall-after", help="Stop after this many steps with no working-tree change."),
    max_iterations: int = typer.Option(
        25, "--max-iterations", help="Agent tool-call budget per step."),
) -> None:
    """Supervised autonomy - work a longer task unattended, with guardrails.

    This is NOT blind overnight magic. ronin runs the coding agent step by step
    and bounds it: every step is CHECKPOINTED (revertible with `ronin rewind`),
    the repo's own tests are run as a GATE after each change (stop after N
    failing gates in a row), and the loop STALL-STOPS if the tree stops changing.
    At the end it reports what it did, what passed/failed, and how to revert.

    Honest limits: the agent can still make a wrong-but-passing change, and the
    gate is only as good as your tests. Review the result; revert with the
    printed checkpoint ids if you don't like it.
    """
    from .auto import run_auto_loop
    from .checkpoint import create_checkpoint
    from .code_mode import run_code_agent
    from .worktree import is_git_repo

    goal = " ".join(task)
    config = load_config()
    if not config.has_provider_auth():
        console.print("[yellow]ronin auto needs a provider key[/yellow] - run [bold]ronin login[/bold].")
        raise typer.Exit(2)

    git = is_git_repo(root)
    if not git:
        console.print("[yellow]not a git repo[/yellow] - running without checkpoints. "
                      "changes will NOT be auto-revertible; review by hand.")

    console.print(f"[#7aa2f7]ronin auto[/#7aa2f7] [dim]supervised autonomy[/dim] "
                  f"[bold]{goal[:80]}[/bold]")
    console.print(f"[dim]rails: <={steps} steps, stop after {max_failures} failing "
                  f"gates in a row, stall-stop after {stall_after} idle steps[/dim]")

    def _agent(prompt: str) -> None:
        run_code_agent(config, prompt, root=root, console=console, yolo=True,
                       max_iterations=max_iterations)

    def _checkpoint(r, label: str):
        return create_checkpoint(r, label).id

    def _on_step(step) -> None:
        if step.verdict is not None and step.verdict.ran:
            mark = "[green]green[/green]" if step.verdict.passed else "[#f7768e]red[/#f7768e]"
            gate = f" gate {mark} [dim]{step.verdict.summary}[/dim]"
        elif step.changed:
            gate = " [dim](no tests detected)[/dim]"
        else:
            gate = " [dim](no change)[/dim]"
        cp = f" cp#{step.checkpoint_id}" if step.checkpoint_id is not None else ""
        console.print(f"[#6b7089]step {step.n}[/#6b7089]{cp}{gate}")
        if step.note:
            console.print(f"  [yellow]{step.note}[/yellow]")

    report = run_auto_loop(
        root, goal, _agent,
        max_steps=steps, max_consecutive_failures=max_failures, stall_after=stall_after,
        checkpoint_fn=(_checkpoint if git else None), on_step=_on_step,
    )
    report.is_git = git

    # final report: what it did, what passed/failed, how to revert
    console.print(f"\n[bold]ronin auto report[/bold] [dim]- {report.stopped_reason}[/dim]")
    console.print(f"  steps run: {len(report.steps)}")
    if report.final_passed is True:
        console.print("  tests: [green]passing[/green]")
    elif report.final_passed is False:
        console.print("  tests: [#f7768e]failing[/#f7768e]")
    else:
        console.print("  tests: [dim]not run (no test command detected)[/dim]")
    console.print(f"  revert: [dim]{report.revert_hint()}[/dim]")

    raise typer.Exit(0 if report.final_passed is not False else 1)


# ---------- grep (search code by intent) ----------

@dev_app.command()
def grep(
    query: list[str] = typer.Argument(..., help="What you're looking for, in plain words."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
    limit: int = typer.Option(12, "--limit", help="How many candidates to rank."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Just show ranked locations, don't ask the agent."),
) -> None:
    """🔎 Search by intent, not by string — "where do we handle retries?" ranks the
    functions/classes most relevant to your question (name + docstring + body),
    then the agent reads the winners and answers precisely.
    """
    from .intent_search import search, search_prompt

    q = " ".join(query)
    hits = search(root, q, limit=limit)
    if not hits:
        console.print(f"[yellow]nothing matched[/yellow] [bold]{q}[/bold] "
                      "[dim](try different words?)[/dim]")
        raise typer.Exit(1)
    console.print(f"[#7aa2f7]🔎 \"{q}\"[/#7aa2f7] [dim]— top matches:[/dim]")
    top = hits[0][1] or 1.0
    for u, s in hits:
        bars = "█" * max(1, round(5 * s / top))
        console.print(f"  [#6b7089]{bars:<5}[/#6b7089] [cyan]{u.file}:{u.line}[/cyan] "
                      f"[dim]{u.kind}[/dim] [bold]{u.name}[/bold]")

    if no_ai:
        return
    config = load_config()
    if not config.has_provider_auth():
        console.print("[dim]set a provider to have ronin read the winners and answer.[/dim]")
        return
    from .code_mode import run_code_agent
    console.print("\n[#6b7089]reading the most relevant code…[/#6b7089]\n")
    run_code_agent(config, search_prompt(q, hits), root=root, console=console,
                   read_only=True, include_image_tool=False, max_iterations=12)


# ---------- commit (stage + Conventional Commit message) ----------

@dev_app.command()
def commit(
    all_changes: bool = typer.Option(True, "--all/--staged", "-a", help="Stage all tracked changes first (default), or commit only what's staged."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the message; don't commit."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Use the offline heuristic message instead of the agent."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """✍️  Write a Conventional Commit from your diff — infers type + scope from the
    changed files, drafts a clean subject + body grounded in the actual changes,
    and commits. --dry-run to preview, --no-ai for the offline heuristic.
    """
    import subprocess

    from .commit_msg import commit_prompt, fallback_subject, infer_type

    def _git(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, timeout=60)

    if _git(["rev-parse", "--git-dir"]).returncode != 0:
        console.print("[red]not a git repository.[/red]")
        raise typer.Exit(1)
    if all_changes and not dry_run:
        _git(["add", "-A"])

    names = _git(["diff", "--cached", "--name-only"]).stdout.split()
    patch = _git(["diff", "--cached"]).stdout
    if not names:
        console.print("[yellow]nothing staged to commit.[/yellow] [dim](edit files, or drop --staged)[/dim]")
        raise typer.Exit(1)

    summary = f"{len(names)} file(s): {', '.join(names[:6])}" + (" …" if len(names) > 6 else "")
    console.print(f"[#7aa2f7]✍️  {summary}[/#7aa2f7]")

    message = ""
    config = load_config()
    if not no_ai and config.has_provider_auth():
        try:
            from ronin_agent_patterns import Message

            from .runner import build_single_provider
            provider = build_single_provider(config)
            with console.status("[dim] drafting commit message…[/dim]", spinner="dots"):
                resp = provider.complete(
                    system="You write clean, conventional git commit messages.",
                    messages=[Message(role="user", content=commit_prompt(summary, patch, infer_type(names)))],
                    tools=[], max_tokens=400)
            message = (resp.text or "").strip().strip("`")
        except Exception as e:  # noqa: BLE001
            console.print(f"[dim]AI draft failed ({e}); using heuristic.[/dim]")
    if not message:
        message = fallback_subject(names)

    from rich.panel import Panel
    console.print(Panel(message, title="commit message", border_style="#6b7089", padding=(1, 2)))
    if dry_run:
        console.print("[dim]--dry-run: not committing.[/dim]")
        return
    r = _git(["commit", "-m", message])
    if r.returncode == 0:
        short = _git(["rev-parse", "--short", "HEAD"]).stdout.strip()
        console.print(f"[green]✓ committed[/green] [bold]{short}[/bold]")
        try:  # auto-earn XP for a real unit of progress (best-effort, never blocks;
            # gamification lives in the optional ronin-arcade extra)
            from ronin_arcade.gamify import record
            record("commit")
        except Exception:  # noqa: BLE001
            pass
    else:
        console.print(f"[red]commit failed:[/red] {(r.stderr or r.stdout).strip()[:300]}")
        raise typer.Exit(1)


# ---------- lint (detect + run + optional --fix) ----------

@dev_app.command()
def lint(
    fix: bool = typer.Option(False, "--fix", help="Apply safe autofixes, then have ronin fix the rest."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🧹 Run the project's linter (ruff/flake8/eslint, auto-detected) and report
    the issue count. With --fix, apply the linter's own autofixes then hand any
    remaining failures to ronin to repair.
    """
    import shutil
    import subprocess

    from .lint import detect_linter, fix_command, parse_issue_count

    avail = {t for t in ("ruff", "flake8", "npx", "eslint") if shutil.which(t)}
    cmd = detect_linter(root, available=avail)
    if not cmd:
        console.print("[yellow]no linter detected[/yellow] [dim](install ruff: "
                      "[bold]uv tool install ruff[/bold])[/dim]")
        raise typer.Exit(1)
    cmd_str = " ".join(cmd)

    if fix:
        fc = fix_command(cmd)
        if fc:
            console.print(f"[#6b7089]applying safe autofixes:[/#6b7089] [cyan]{' '.join(fc)}[/cyan]")
            try:
                subprocess.run(fc, cwd=str(root), capture_output=True, text=True, timeout=180)
            except (OSError, subprocess.SubprocessError):
                pass

    console.print(f"[#7aa2f7]🧹 linting[/#7aa2f7] [cyan]{cmd_str}[/cyan]")
    try:
        r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        console.print(f"[red]couldn't run linter: {e}[/red]")
        raise typer.Exit(1)
    output = (r.stdout or "") + (r.stderr or "")
    n = parse_issue_count(output, cmd)
    if r.returncode == 0 and n == 0:
        console.print("[green]✓ clean — no lint issues.[/green]")
        return
    console.print(f"[#f7768e]✗ {n or 'some'} issue(s) remain[/#f7768e]")
    snippet = "\n".join(output.splitlines()[:20])
    console.print(f"[dim]{snippet}[/dim]")
    if not fix:
        console.print("\n[dim]add [bold]--fix[/bold] to autofix + have ronin repair the rest.[/dim]")
        return
    config = load_config()
    if not config.has_provider_auth():
        console.print("[dim]set a provider to have ronin fix the remaining issues.[/dim]")
        return
    from .code_mode import run_code_agent
    console.print("\n[#6b7089]handing the remaining lint errors to ronin…[/#6b7089]\n")
    run_code_agent(
        config,
        f"`{cmd_str}` still reports lint issues after autofix. Run it, read the "
        "errors, and fix them properly (not by suppressing). Re-run to confirm clean.\n\n"
        f"Current output:\n{output[:6000]}",
        root=root, console=console, max_iterations=20)


# ---------- checkup (repo health report) ----------

@dev_app.command()
def checkup(
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🩺 Full repo health checkup — README, license, tests, dependency hygiene,
    committed secrets, TODO load, oversized files — rolled into a health grade.
    (Distinct from [bold]ronin doctor[/bold], which checks your config/provider.)
    """
    from .checkup import grade, health_score, run_checks

    checks = run_checks(root)
    score = health_score(checks)
    g = grade(score)
    color = "green" if g in {"A", "B"} else "#e0af68" if g in {"C", "D"} else "#f7768e"
    console.print(f"[{color}]🩺 health [bold]{g}[/bold]  ({score}/100)[/{color}]\n")
    icon = {"ok": "[green]✓[/green]", "warn": "[#e0af68]●[/#e0af68]", "fail": "[#f7768e]✗[/#f7768e]"}
    for c in checks:
        console.print(f"  {icon.get(c.status, '·')} [bold]{c.name:<13}[/bold] [dim]{c.detail}[/dim]")
    fails = [c for c in checks if c.status == "fail"]
    if fails:
        console.print(f"\n[#f7768e]{len(fails)} critical issue(s) to address first.[/#f7768e]")
    elif score == 100:
        console.print("\n[green]spotless. ship it. 🚀[/green]")


# ---------- complexity (cyclomatic hotspots) ----------

@dev_app.command()
def complexity(
    threshold: int = typer.Option(10, "--threshold", "-t", help="Minimum complexity to report."),
    limit: int = typer.Option(25, "--limit", help="Max functions to list."),
    refactor: bool = typer.Option(False, "--refactor", help="Have ronin simplify the worst offender."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🌀 Rank your most complex functions by cyclomatic complexity (branches per
    function) — the hardest code to test and maintain. --refactor hands the worst
    one to ronin to simplify without changing behaviour.
    """
    from .complexity import find_complex, rating, refactor_prompt

    hits = find_complex(root, threshold=threshold, limit=limit)
    if not hits:
        console.print(f"[green]✓ nothing at or above complexity {threshold} — tidy.[/green]")
        return
    console.print(f"[#7aa2f7]🌀 {len(hits)} function(s) ≥ complexity {threshold}[/#7aa2f7] "
                  "[dim](worst first)[/dim]")
    worst = hits[0].score or 1
    for c in hits:
        bars = "█" * max(1, round(8 * c.score / worst))
        tone = "#f7768e" if c.score >= 20 else "#e0af68" if c.score >= 10 else "#6b7089"
        console.print(f"  [{tone}]{c.score:>3}[/{tone}] [dim]{bars:<8}[/dim] "
                      f"[cyan]{c.file}:{c.line}[/cyan] [bold]{c.name}[/bold] "
                      f"[dim]{rating(c.score)}[/dim]")

    if not refactor:
        console.print("\n[dim]add [bold]--refactor[/bold] to have ronin simplify the worst one.[/dim]")
        return
    config = load_config()
    if not config.has_provider_auth():
        console.print("[dim]set a provider to refactor.[/dim]")
        return
    from .code_mode import run_code_agent
    console.print(f"\n[#6b7089]refactoring[/#6b7089] [bold]{hits[0].name}[/bold] "
                  f"[dim](complexity {hits[0].score})[/dim]\n")
    run_code_agent(config, refactor_prompt(hits[0]), root=root, console=console,
                   max_iterations=20)


# ---------- api (markdown API reference) ----------

@dev_app.command()
def api(
    out: Path = typer.Option(None, "--out", "-o", help="Write the reference to a file (default: print to stdout)."),
    title: str = typer.Option("API Reference", "--title", help="Document title."),
    root: Path = typer.Option(Path("."), "--root", help="Package/repo root."),
) -> None:
    """📚 Generate a Markdown API reference from public functions/classes and their
    docstrings — no Sphinx, no config. Print it, or write it with --out.
    """
    from .api_ref import build_api, render_markdown

    modules = build_api(root)
    if not modules:
        console.print("[yellow]no public API found[/yellow] [dim](no public defs, or all tests?)[/dim]")
        raise typer.Exit(1)
    md = render_markdown(modules, title=title)
    total = sum(len(s) for _, s in modules)
    if out:
        try:
            out.write_text(md, encoding="utf-8")
        except OSError as e:
            console.print(f"[red]write failed:[/red] {e}")
            raise typer.Exit(1)
        console.print(f"[green]✓[/green] {total} symbol(s) across {len(modules)} module(s) "
                      f"→ [cyan]{out}[/cyan]")
    else:
        from rich.markdown import Markdown
        console.print(Markdown(md))


# ---------- subnet (CIDR calculator) ----------

# ---------- passphrase (memorable strong passwords) ----------

# ---------- toc (markdown table of contents) ----------

# ---------- jwt (decode a JSON Web Token) ----------

# ---------- book (research & prepare a booking; you confirm and pay) ----------

def _book_default_search():
    """The web_search function for ``ronin book``, or None when unavailable.

    Imported lazily so a missing/odd web stack never breaks the command.
    """
    try:
        from .web_tools import web_search
    except Exception:  # noqa: BLE001
        return None
    return lambda q, n: web_search(q, max_results=n)


def _book_browser_prefill(request, result):
    """Drive the browser to pre-fill a booking form up to but NOT past payment.

    Optional and lazy: returns False (no pre-fill) when the browser extra is not
    installed. Every intended browser action is routed through the payment guard
    first, so a pay/submit step can never reach the browser.
    """
    from .book import assert_not_payment
    from .browser_tools import browse_navigate, browse_read, browser_tools_available

    if not browser_tools_available():
        return False
    link = result.next_step_link
    if not link:
        return False
    # Only ever navigate and read. We never click a pay/submit control. Guard
    # the intent explicitly so the rule is enforced, not just implied.
    assert_not_payment("navigate to booking page")
    assert_not_payment("read booking page")
    browse_navigate(link)
    browse_read()
    # We deliberately stop here: the user enters details and pays themselves.
    return True


@util_app.command()
def book(
    request: str = typer.Argument(..., help='What to book, e.g. "a flight SFO to JFK on Friday".'),
    no_browser: bool = typer.Option(False, "--no-browser", help="Skip the browser; search + summary only."),
    max_results: int = typer.Option(5, "--max", "-n", help="How many options to gather."),
) -> None:
    """🧳 Research and PREPARE a booking, then stop. You confirm and pay yourself.

    Gathers options (option · price · link · details) via web search, optionally
    pre-fills a form in the browser up to the payment step, and hands you a
    summary plus the next-step link. It NEVER submits a payment or final
    purchase. That is a hard rule. You always do the payment.
    """
    from .book import prepare_booking

    search_fn = _book_default_search()
    prefill_fn = None if no_browser else _book_browser_prefill

    result = prepare_booking(
        request,
        search_fn=search_fn,
        browser_prefill_fn=prefill_fn,
        max_results=max_results,
    )

    console.print(f"[bold #7aa2f7]🧳 Prepared (not paid):[/bold #7aa2f7] {result.request}")
    if result.options:
        from rich.table import Table as _Table

        table = _Table(box=box.SIMPLE, show_header=True, header_style="dim")
        table.add_column("#", style="dim", width=2)
        table.add_column("Option")
        table.add_column("Price", style="#9ece6a")
        table.add_column("Link", style="#7dcfff", overflow="fold")
        for i, o in enumerate(result.options, 1):
            table.add_row(str(i), o.option, o.price or "-", o.link or "-")
        console.print(table)
    else:
        console.print("[yellow]No options gathered.[/yellow]")

    if result.next_step_link:
        console.print(f"\n[bold]Next step (you confirm and pay):[/bold] "
                      f"[#7dcfff]{result.next_step_link}[/#7dcfff]")
    console.print("\n[dim]Manual steps:[/dim]")
    for step in result.manual_steps:
        console.print(f"  [dim]·[/dim] {step}")
    for note in result.notes:
        console.print(f"[dim]note: {note}[/dim]")
    console.print("\n[bold #e0af68]ronin never pays. You confirm and pay yourself.[/bold #e0af68]")


# ---------- research (search the web and answer) ----------

@util_app.command()
def research(
    question: str = typer.Argument(..., help='What to look up, e.g. "who won the 2022 world cup".'),
) -> None:
    """🔎 Search the web and answer a question, with sources. Free, no key.

    Runs the read-only agent with the keyless web tools (web_search + fetch_url):
    it searches the web, reads the most relevant pages, and answers. If the agent
    stack is unavailable it degrades to a raw web search so you still get links.
    """
    from .research import run_research

    config = load_config()
    root = Path.home()
    answer = run_research(question, config=config, root=root)
    console.print(answer)


# ---------- uuid (generate / inspect) ----------

# ---------- hash (digests / checksums) ----------

# ---------- curl2code (curl -> code) ----------

# ---------- count (wc-like) ----------

# ---------- api-test (assert on an endpoint) ----------

@dev_app.command(name="api-test")
def api_test_cmd(
    url: str = typer.Argument(..., help="The endpoint to test."),
    status: int = typer.Option(None, "--status", "-s", help="Expected HTTP status code."),
    field: Optional[list[str]] = typer.Option(None, "--field", "-f", help="Assert a JSON field: path=value (e.g. data.0.id=5). Repeatable."),
) -> None:
    """🧪 Smoke-test an API endpoint: assert its status and JSON fields. Exits
    non-zero if any assertion fails (handy in scripts/CI).
    """
    import time

    import httpx

    from .api_test import check_response, parse_expect
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    t0 = time.time()
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]request failed:[/red] {e}")
        raise typer.Exit(2)
    ms = round((time.time() - t0) * 1000)
    body = None
    try:
        body = r.json()
    except ValueError:
        pass
    console.print(f"[#7aa2f7]🧪 {url}[/#7aa2f7] [dim]→ {r.status_code} · {ms}ms[/dim]")
    outcome = check_response(r.status_code, body, expect_status=status, expect=parse_expect(list(field or [])))
    for res in outcome["results"]:
        mark = "[green]✓[/green]" if res["passed"] else "[#f7768e]✗[/#f7768e]"
        detail = "" if res["passed"] else f" [dim](got {res['actual']!r})[/dim]"
        console.print(f"  {mark} {res['check']}{detail}")
    if not outcome["results"]:
        console.print("  [dim]no assertions — pass --status or --field to check something.[/dim]")
    if not outcome["passed"]:
        console.print("\n[#f7768e]FAILED[/#f7768e]")
        raise typer.Exit(1)
    console.print("\n[green]PASSED[/green]")


# ---------- user-agent (parse a UA string) ----------

# ---------- env-example (.env -> .env.example) ----------

@dev_app.command(name="env-example")
def env_example_cmd(
    source: str = typer.Option(".env", "--from", help="The .env file to read."),
    write: bool = typer.Option(False, "--write", help="Write to .env.example."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """🔑 Generate a safe .env.example from your .env — keeps keys + safe defaults,
    blanks the secret values so you can commit a template. Offline.
    """
    from .env_example import mask_env

    src = Path(root) / source
    if not src.is_file():
        console.print(f"[yellow]no {source} found[/yellow] at {src}")
        raise typer.Exit(1)
    result = mask_env(src.read_text(encoding="utf-8"))
    if write:
        out = Path(root) / ".env.example"
        out.write_text(result["text"], encoding="utf-8")
        console.print(f"[green]✓[/green] wrote [cyan]{out}[/cyan] [dim]({len(result['keys'])} keys)[/dim]")
    else:
        console.print(result["text"])
        console.print(f"[dim]{len(result['keys'])} keys · secret values blanked[/dim]")


# ---------- time (world clock / timezone) ----------

# ---------- http (explain a status code) ----------

# ---------- redact (strip secrets/PII from text) ----------

# ---------- diff-json (structural JSON diff) ----------

# ---------- chmod (explain unix permissions) ----------

# ---------- json (query JSON with a path) ----------

# ---------- license (generate a LICENSE) ----------

# ---------- gitignore (generate from templates) ----------

# ---------- cron (explain a cron expression) ----------

# ---------- schedule (store / list / run-due tasks) ----------

schedule_app = typer.Typer(
    help="A real task scheduler: store agent tasks on a cron schedule, list them, "
         "and run the ones that are due. Offline-capable (no key, no network).",
)
util_app.add_typer(schedule_app, name="schedule")


@schedule_app.command("add")
def schedule_add(
    name: str = typer.Argument(..., help="Short task name, e.g. 'morning-briefing'."),
    prompt: str = typer.Argument(..., help="The prompt ronin should run when the task fires."),
    cron: str = typer.Option(..., "--cron", help="A 5-field cron expression or @alias, e.g. '0 9 * * 1-5' (quote it)."),
) -> None:
    """⏱️ Store a task: a prompt plus a cron schedule. Saved to ~/.ronin/schedule.json."""
    from .cron_describe import describe_cron
    from .scheduler import add_task

    try:
        task = add_task(name, prompt, cron)
    except ValueError as e:
        console.print(f"[#f7768e]{e}[/#f7768e]")
        raise typer.Exit(1)
    console.print(f"[green]✓ stored[/green] [cyan]{task.name}[/cyan] [dim]({task.cron} - {describe_cron(task.cron)})[/dim]")
    console.print(f"   [dim]run due tasks with [bold]ronin schedule run-due[/bold] (or wire it to cron once a minute).[/dim]")


@schedule_app.command("list")
def schedule_list() -> None:
    """📋 List stored tasks with their schedule and next run time."""
    from datetime import datetime

    from rich.table import Table

    from .scheduler import load_tasks, next_run_after

    tasks = load_tasks()
    if not tasks:
        console.print("[dim]no scheduled tasks yet - add one with [bold]ronin schedule add[/bold].[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name", style="cyan")
    table.add_column("cron")
    table.add_column("next run")
    table.add_column("runs", justify="right")
    table.add_column("prompt", overflow="fold")
    now = datetime.now()
    for t in sorted(tasks, key=lambda x: x.name):
        nxt = next_run_after(t.cron, now)
        nxt_s = nxt.strftime("%Y-%m-%d %H:%M") if nxt else "-"
        flag = "" if t.enabled else " [dim](disabled)[/dim]"
        table.add_row(t.name + flag, t.cron, nxt_s, str(t.runs), t.prompt[:60])
    console.print(table)


@schedule_app.command("remove")
def schedule_remove(
    name: str = typer.Argument(..., help="Name of the task to remove."),
) -> None:
    """🗑️ Remove a stored task by name."""
    from .scheduler import remove_task

    if remove_task(name):
        console.print(f"[green]✓ removed[/green] [cyan]{name}[/cyan]")
    else:
        console.print(f"[dim]no task named {name!r}.[/dim]")
        raise typer.Exit(1)


@schedule_app.command("run-due")
def schedule_run_due(
    at: str = typer.Option(None, "--at", help="Evaluate due tasks at this time (YYYY-MM-DD HH:MM) instead of now."),
) -> None:
    """🏃 Run every task that is due right now through ronin's agent.

    Uses the configured provider, falling back to the offline demo brain when no
    key is set, so this works at $0 with no network. Wire it to the system
    crontab (``ronin nightshift --schedule '* * * * *'`` style) to fire on time,
    or invoke it by hand.
    """
    from datetime import datetime

    from .scheduler import due_tasks, load_tasks, run_due

    now = datetime.now()
    if at:
        try:
            now = datetime.strptime(at.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            console.print("[#f7768e]--at must look like 'YYYY-MM-DD HH:MM'[/#f7768e]")
            raise typer.Exit(1)

    pending = due_tasks(load_tasks(), now)
    if not pending:
        console.print(f"[dim]nothing due at {now.strftime('%Y-%m-%d %H:%M')}.[/dim]")
        return
    console.print(f"[#7aa2f7]🏃 running {len(pending)} due task(s)…[/#7aa2f7]")
    config = load_config()
    results = run_due(config, now)
    for r in results:
        if r.success:
            console.print(f"[green]✓[/green] [cyan]{r.name}[/cyan]")
            if r.output.strip():
                console.print(f"   [dim]{r.output.strip()[:300]}[/dim]")
        else:
            console.print(f"[#f7768e]✗ {r.name}[/#f7768e] [dim]{(r.error or '')[:160]}[/dim]")


# ---------- mock (fixture data from a schema) ----------

# ---------- schema / endpoint (API toolkit) ----------

@dev_app.command()
def endpoint(
    url: str = typer.Argument(..., help="An HTTP(S) endpoint to probe."),
) -> None:
    """🛰️  Probe an API endpoint — status, latency, content-type, inferred response
    schema, and a ready-to-run `ronin plugin from-api` command to turn it into a tool.
    """
    import json
    import time

    import httpx

    from .schema_infer import infer_schema, leaf_paths
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    console.print(f"[#7aa2f7]🛰️  probing[/#7aa2f7] [bold]{url}[/bold]")
    t0 = time.time()
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]request failed:[/red] {e}")
        raise typer.Exit(1)
    ms = round((time.time() - t0) * 1000)
    ok = "[green]" if r.is_success else "[#f7768e]"
    console.print(f"  {ok}{r.status_code}[/] · {ms}ms · {r.headers.get('content-type', '?')[:40]} · {len(r.content)} bytes")
    try:
        data = r.json()
    except ValueError:
        console.print("  [dim]non-JSON response — no schema to infer.[/dim]")
        return
    console.print("\n[bold]inferred schema[/bold]")
    console.print_json(json.dumps(infer_schema(data)))
    paths = leaf_paths(data)
    if paths:
        flags = " ".join(f"-f {p}" for p in paths[:5])
        console.print(f"\n[dim]make it a tool:[/dim]\n  [cyan]ronin plugin from-api mytool '{url}' {flags}[/cyan]")


# ---------- tree (annotated project map) ----------

@dev_app.command()
def tree(
    depth: int = typer.Option(3, "--depth", "-d", help="How many directory levels to show."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🌳 An annotated project map — directories with code-file and line counts,
    noise folders skipped. Grok a repo's shape in one screen.
    """
    from .tree_map import biggest_dirs, build_tree, render_lines

    node = build_tree(root, max_depth=depth)
    if node.files == 0:
        console.print("[yellow]no code files found[/yellow] [dim](empty repo, or all skipped?)[/dim]")
        return
    for i, line in enumerate(render_lines(node)):
        if i == 0:
            console.print(f"[#7aa2f7]🌳 {line}[/#7aa2f7]")
        else:
            console.print(f"[#6b7089]{line}[/#6b7089]", highlight=False)
    top = biggest_dirs(node)
    if top:
        chips = "  ".join(f"[cyan]{n}[/cyan] [dim]{ln:,}[/dim]" for n, ln in top)
        console.print(f"\n[dim]heaviest:[/dim] {chips}")


# ---------- deadcode (probably-unused symbols) ----------

@dev_app.command()
def deadcode(
    include_decorated: bool = typer.Option(False, "--include-decorated", help="Also flag decorated functions (CLI commands, fixtures, routes)."),
    limit: int = typer.Option(100, "--limit", help="Max candidates to list."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🪦 Find probably-unused code — public functions/classes whose name is never
    referenced anywhere in the repo. Candidates to verify, not certainties:
    decorated entry points, __all__ exports, tests and main are skipped; dynamic
    dispatch can't be seen.
    """
    from .deadcode import find_dead

    dead = find_dead(root, include_decorated=include_decorated, limit=limit)
    if not dead:
        console.print("[green]✓ no obviously-unused public symbols — lean.[/green]")
        return
    console.print(f"[#7aa2f7]🪦 {len(dead)} unused candidate(s)[/#7aa2f7] "
                  "[dim](verify before deleting — dynamic use can't be detected)[/dim]")
    by_file: dict[str, list] = {}
    for d in dead:
        by_file.setdefault(d.file, []).append(d)
    for f, syms in by_file.items():
        console.print(f"\n  [cyan]{f}[/cyan]")
        for d in syms:
            tag = " [#e0af68](decorated)[/#e0af68]" if d.decorated else ""
            console.print(f"    [dim]{d.line:>4}[/dim]  [#6b7089]{d.kind}[/#6b7089] "
                          f"[bold]{d.name}[/bold]{tag}")


# ---------- release (bump + changelog + tag) ----------

@dev_app.command()
def release(
    kind: str = typer.Argument("patch", help="major | minor | patch."),
    tag: bool = typer.Option(False, "--tag", help="Create a git tag for the new version."),
    write_changelog: bool = typer.Option(True, "--changelog/--no-changelog", help="Prepend a CHANGELOG section."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """Prepare a synchronized Ronin release and optionally create its git tag."""
    from .release import bump_version, current_version, prepare_release, release_version_files, validate_release

    files = list(release_version_files(root))
    cur = current_version(files)
    if not cur:
        console.print("[yellow]couldn't find the Ronin release version.[/yellow]")
        raise typer.Exit(1)
    try:
        new = bump_version(cur, kind)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)
    console.print(f"[#7aa2f7]🚀 release[/#7aa2f7] [bold]{cur} → {new}[/bold]")
    try:
        changed = prepare_release(root, new)
        validate_release(root, f"v{new}")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)
    console.print(f"  [green]✓[/green] synchronized {len(changed)} release version source(s)")

    if write_changelog:
        from .changelog import render_changelog
        from .git_helper import _git
        since = _git(root, "describe", "--tags", "--abbrev=0").stdout.strip() or None
        rng = f"{since}..HEAD" if since else "HEAD"
        log = _git(root, "log", rng, "--pretty=%s", "--no-merges").stdout
        subjects = [ln for ln in log.splitlines() if ln.strip()]
        if subjects:
            section = render_changelog(subjects, version=new)
            cl = Path(root) / "CHANGELOG.md"
            existing = cl.read_text(encoding="utf-8") if cl.is_file() else "# Changelog\n"
            head, _, rest = existing.partition("\n")
            cl.write_text(f"{head}\n\n{section}\n{rest.lstrip()}", encoding="utf-8")
            console.print(f"  [green]✓[/green] CHANGELOG updated ({len(subjects)} commit(s))")
    if tag:
        from .git_helper import _git
        r = _git(root, "tag", f"v{new}")
        console.print(f"  [green]✓ tagged v{new}[/green]" if r.returncode == 0
                      else f"[yellow]couldn't tag: {r.stderr.strip()[:80]}[/yellow]")
    console.print("[dim]review the changes, then commit + push (and the tag).[/dim]")


# ---------- docstring (find + write missing docstrings) ----------

@dev_app.command()
def docstring(
    execute: bool = typer.Option(False, "--execute", help="Write docstrings (default: list gaps)."),
    limit: int = typer.Option(60, "--limit", help="Max symbols."),
    budget: float = typer.Option(None, "--budget", help="USD spend cap."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """📝 Find public functions/classes with NO docstring, then (with --execute)
    write one for each — test-gated, as reviewable patches.
    """
    from .docstring import find_doc_gaps, to_tasks

    gaps = find_doc_gaps(root, limit=limit)
    if not gaps:
        console.print("[green]✓ every public symbol is documented.[/green]")
        return
    by_file: dict[str, int] = {}
    for g in gaps:
        by_file[g.file] = by_file.get(g.file, 0) + 1
    console.print(f"[#7aa2f7]📝 {len(gaps)} undocumented symbol(s)[/#7aa2f7] in {len(by_file)} file(s):")
    for f, n in list(by_file.items())[:20]:
        console.print(f"  [cyan]{f}[/cyan] [dim]{n} symbol(s)[/dim]")
    if not execute:
        console.print("\n[dim]add [bold]--execute[/bold] to write docstrings (reviewable patches).[/dim]")
        return
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .nightshift import run_nightshift, summarize
    console.print("[dim]writing docstrings (isolated worktrees, test-gated)…[/dim]\n")
    results = run_nightshift(config, root, console, to_tasks(gaps), execute=True, budget=budget)
    counts = summarize(results)
    console.print(f"\n[bold]📝 docstrings[/bold] — [green]{counts['patched']} file(s) documented[/green]")
    console.print("[dim]review with [bold]ronin patches[/bold].[/dim]")


# ---------- todo (TODO/FIXME board) ----------

def _todo_to_issues(targets, *, root: Path, only: str, yes: bool) -> None:
    """Draft (and optionally file) a GitHub issue per marker. Dry-run by default."""
    from .gh_helper import create_issue, gh_available
    from .todo import context_lines, marker_to_issue

    wanted = {m.strip().upper() for m in only.split(",") if m.strip()} if only else None
    picked = [t for t in targets if not wanted or t.marker.upper() in wanted]
    if not picked:
        console.print(f"[dim]no markers match --only {only}[/dim]")
        return

    drafts = []
    for t in picked:
        ctx: list[str] = []
        src = Path(root) / t.file
        try:
            lines = src.read_text(encoding="utf-8", errors="ignore").splitlines()
            ctx = context_lines(lines, t.line)
        except OSError:
            ctx = []
        drafts.append(marker_to_issue(t.file, t.line, t.marker, t.text, context=ctx))

    console.print(f"\n[#7aa2f7]drafting {len(drafts)} issue(s)[/#7aa2f7]"
                  + (" [dim](dry-run — add --yes to file them)[/dim]" if not yes else ""))
    for d in drafts:
        console.print(f"  [#e0af68]• {d.title}[/#e0af68] [dim]({', '.join(d.labels)})[/dim]")

    if not yes:
        console.print("\n[dim]nothing filed. add [bold]--yes[/bold] to create these on GitHub.[/dim]")
        return
    if not gh_available():
        console.print("\n[yellow]the GitHub CLI 'gh' isn't installed — printed drafts only.[/yellow] "
                      "[dim]brew install gh && gh auth login[/dim]")
        raise typer.Exit(2)
    filed = 0
    for d in drafts:
        url = create_issue(d.title, d.body, root=root, labels=d.labels)
        if url:
            filed += 1
            console.print(f"  [green]✓[/green] {url}")
        else:
            console.print(f"  [red]✗ failed:[/red] [dim]{d.title}[/dim]")
    console.print(f"\n[bold]filed {filed}/{len(drafts)} issue(s).[/bold]")


@dev_app.command()
def todo(
    execute: bool = typer.Option(False, "--execute", help="Work the TODOs autonomously (Nightshift)."),
    issues: bool = typer.Option(False, "--issues", help="Draft a GitHub issue per marker (dry-run unless --yes)."),
    only: str = typer.Option("", "--only", help="With --issues, limit to these markers (e.g. \"FIXME,HACK\")."),
    yes: bool = typer.Option(False, "--yes", help="With --issues, actually file the issues via gh (no prompt)."),
    duel: str = typer.Option(None, "--duel", help="Red-team each patch with a rival provider."),
    budget: float = typer.Option(None, "--budget", help="USD spend cap."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🗒  A board of every FIXME/TODO/HACK in your code; --execute works them
    autonomously (each in isolation, test-gated) into reviewable patches.

    [bold]--issues[/bold] drafts a GitHub issue per marker (file:line + code
    context) and prints them; add [bold]--yes[/bold] to actually file them via
    [bold]gh[/bold]. Narrow with [bold]--only FIXME,HACK[/bold].
    """
    from .kaizen import find_targets

    targets = find_targets(root, limit=200)
    if not targets:
        console.print("[green]✓ no FIXME/TODO/HACK markers — clean.[/green]")
        return
    by_marker: dict[str, int] = {}
    for t in targets:
        by_marker[t.marker] = by_marker.get(t.marker, 0) + 1
    summary = " · ".join(f"{m}: {n}" for m, n in by_marker.items())
    console.print(f"[#7aa2f7]🗒 {len(targets)} marker(s)[/#7aa2f7] [dim]({summary})[/dim]")
    for t in targets[:25]:
        console.print(f"  [#e0af68]{t.marker:<6}[/#e0af68] [cyan]{t.file}:{t.line}[/cyan] "
                      f"[dim]{t.text.strip()[:55]}[/dim]")

    if issues:
        _todo_to_issues(targets, root=root, only=only, yes=yes)
        return
    if not execute:
        console.print("\n[dim]add [bold]--execute[/bold] to resolve them autonomously "
                      "(reviewable patches via [bold]ronin patches[/bold]).[/dim]")
        return
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .consensus import parse_model_spec
    duel_spec = parse_model_spec(duel) if duel else None
    from .nightshift import gather_tasks, run_nightshift, summarize
    tasks = gather_tasks(root, include_todos=True, limit=200)
    console.print("[dim]working the board (isolated worktrees, test-gated)…[/dim]\n")
    results = run_nightshift(config, root, console, tasks, execute=True,
                             duel_against=duel_spec, budget=budget)
    counts = summarize(results)
    console.print(f"\n[bold]🗒 todo[/bold] — [green]{counts['patched']} resolved[/green], "
                  f"{counts['failed-tests']} failed")


# ---------- scaffold (generate a component) ----------

@dev_app.command()
def scaffold(
    what: str = typer.Argument(..., help="What to scaffold, e.g. \"a RetryClient with backoff\"."),
    yolo: bool = typer.Option(False, "--yolo", help="Auto-approve the writes."),
    root: Path = typer.Option(Path("."), "--root", help="Project directory."),
) -> None:
    """🏗  Generate a new component that FITS your project — ronin detects your stack
    and conventions, then writes the component + its test to match (not generic
    boilerplate). Diffs are gated unless --yolo.
    """
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .code_mode import run_code_agent
    from .scaffold import detect_stack, scaffold_prompt

    stack = detect_stack(root)
    console.print(f"[#7aa2f7]🏗 scaffolding[/#7aa2f7] [dim]{what[:70]}[/dim] "
                  f"[#6b7089]· {', '.join(stack)}[/#6b7089]")
    result = run_code_agent(config, scaffold_prompt(what, stack), root=root, console=console,
                            yolo=yolo, max_iterations=20)
    if result.blocked:
        console.print(f"[red]✗[/red] {result.output}")
        raise typer.Exit(2)
    console.print("[bold green]✅ scaffolded[/bold green]")


# ---------- estimate (scope a task before doing it) ----------

@dev_app.command()
def estimate(
    task: str = typer.Argument(..., help="The task to size up."),
    root: Path = typer.Option(Path("."), "--root", help="Repo to scope against."),
) -> None:
    """📊 Scope a task before doing it — ronin explores read-only and reports its
    T-shirt size, files affected, risk, and a projected $ cost on your model.
    """
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .code_mode import run_code_agent
    from .estimate import ESTIMATE_PROMPT, parse_estimate, project_cost

    console.print(f"[#7aa2f7]📊 estimating[/#7aa2f7] [dim]{task[:80]}[/dim]")
    result = run_code_agent(config, ESTIMATE_PROMPT.format(task=task), root=root,
                            console=console, read_only=True, include_image_tool=False, max_iterations=10)
    est = parse_estimate(result.output or "")
    cost = project_cost(est.size, config.provider, config.resolved_model())
    _risk_c = {"low": "green", "medium": "#e0af68", "high": "#f7768e"}.get(est.risk, "dim")
    console.print()
    console.print(f"  size   [bold]{est.size}[/bold]   files ~[bold]{est.files}[/bold]   "
                  f"risk [{_risk_c}]{est.risk}[/{_risk_c}]")
    cost_str = "free" if cost == 0 else f"~${cost:.4f}"
    console.print(f"  projected cost on {config.provider}: [bold]{cost_str}[/bold] "
                  "[dim](rough)[/dim]")


# ---------- spec (design-doc-first) ----------

@dev_app.command()
def spec(
    feature: str = typer.Argument(..., help="The feature/change to spec out."),
    out: str = typer.Option(None, "--out", help="Write the spec to this file (e.g. SPEC.md)."),
    root: Path = typer.Option(Path("."), "--root", help="Repo to ground the spec in."),
) -> None:
    """📐 Design-doc-first — ronin explores the repo (read-only) and writes a
    concrete design spec (problem · approach · files to change · edge cases · test
    plan · open questions) before any code is written. --out saves it.
    """
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .code_mode import run_code_agent
    from .spec import spec_prompt

    console.print(f"[#7aa2f7]📐 speccing[/#7aa2f7] [dim]{feature[:80]}[/dim]")
    result = run_code_agent(config, spec_prompt(feature), root=root, console=console,
                            read_only=True, include_image_tool=False, max_iterations=12)
    if out and result.output:
        try:
            Path(out).write_text(result.output.strip() + "\n", encoding="utf-8")
            console.print(f"\n[green]✓ spec written →[/green] [cyan]{out}[/cyan]")
        except OSError as e:
            console.print(f"[red]couldn't write {out}: {e}[/red]")


# ---------- diagram (architecture map) ----------

@dev_app.command()
def diagram(
    pkg: str = typer.Option(None, "--pkg", help="Package directory (default: auto-detect the biggest)."),
    out: str = typer.Option(None, "--out", help="Write the Mermaid diagram to this file."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🗺  Architecture map — extract the real import graph between your modules and
    render it as Mermaid (paste into Markdown/GitHub). Deterministic; no model needed.
    """
    from .diagram import detect_package, import_graph, stats, to_mermaid

    pkg_dir = Path(pkg) if pkg else detect_package(root)
    if not pkg_dir or not pkg_dir.is_dir():
        console.print("[yellow]couldn't find a Python package — pass --pkg <dir>.[/yellow]")
        raise typer.Exit(1)
    graph = import_graph(pkg_dir)
    if not graph:
        console.print(f"[dim]no modules in {pkg_dir}.[/dim]")
        return
    s = stats(graph)
    mer = to_mermaid(graph, title=str(pkg_dir.name))
    console.print(f"[#7aa2f7]🗺 {s['modules']} modules · {s['edges']} import edges[/#7aa2f7] "
                  f"[dim]({pkg_dir})[/dim]")
    if s["most_depended"]:
        console.print(f"  [dim]most depended-on:[/dim] [bold]{', '.join(s['most_depended'])}[/bold]")
    if out:
        try:
            Path(out).write_text(mer + "\n", encoding="utf-8")
            console.print(f"[green]✓ wrote diagram →[/green] [cyan]{out}[/cyan]")
        except OSError as e:
            console.print(f"[red]couldn't write {out}: {e}[/red]")
    else:
        import sys
        sys.stdout.write(mer + "\n")


# ---------- coverage (find + fill test gaps) ----------

@dev_app.command()
def coverage(
    execute: bool = typer.Option(False, "--execute", help="Actually write tests (default: list the gaps)."),
    limit: int = typer.Option(40, "--limit", help="Max symbols."),
    duel: str = typer.Option(None, "--duel", help="Red-team each test with a rival provider."),
    budget: float = typer.Option(None, "--budget", help="USD spend cap."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🧪 Find public functions/classes with NO test coverage, then (with --execute)
    write a passing test for each — in isolation, test-gated, as reviewable patches.
    """
    from .coverage import find_gaps, to_tasks

    gaps = find_gaps(root, limit=limit)
    if not gaps:
        console.print("[green]✓ every public symbol is referenced in a test.[/green]")
        return
    by_file: dict[str, list[str]] = {}
    for g in gaps:
        by_file.setdefault(g.file, []).append(g.symbol)
    console.print(f"[#7aa2f7]🧪 {len(gaps)} untested symbol(s)[/#7aa2f7] in {len(by_file)} file(s):")
    for f, syms in list(by_file.items())[:20]:
        console.print(f"  [cyan]{f}[/cyan] [dim]{', '.join(syms[:8])}"
                      f"{' …' if len(syms) > 8 else ''}[/dim]")
    if not execute:
        console.print("\n[dim]add [bold]--execute[/bold] to write a passing test for each "
                      "(reviewable patches via [bold]ronin patches[/bold]).[/dim]")
        return
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .consensus import parse_model_spec
    duel_spec = parse_model_spec(duel) if duel else None
    from .nightshift import run_nightshift, summarize
    console.print("[dim]writing tests (isolated worktrees, test-gated)…[/dim]\n")
    results = run_nightshift(config, root, console, to_tasks(gaps), execute=True,
                             duel_against=duel_spec, budget=budget)
    counts = summarize(results)
    console.print(f"\n[bold]🧪 coverage[/bold] — [green]{counts['patched']} test(s) added[/green], "
                  f"{counts['failed-tests']} failed, {counts['no-change']} no-change")
    console.print("[dim]review with [bold]ronin patches[/bold].[/dim]")


# ---------- tournament (single-elim model bracket) ----------

@util_app.command()
def tournament(
    question: str = typer.Argument(..., help="The question the models compete on."),
    models: str = typer.Option(..., "--models", "-m", help="Contenders, e.g. 'anthropic,gemini,cerebras,groq'."),
    judge: str = typer.Option(None, "--judge", help="provider[:model] judge (default: current)."),
) -> None:
    """🏟  A single-elimination bracket — models face off pairwise, a judge picks
    each winner, advancing round by round until one champion remains.
    """
    from .bracket import run_tournament
    from .consensus import parse_model_spec

    config = load_config()
    specs = [parse_model_spec(s) for s in models.split(",") if s.strip()]
    if len(specs) < 2:
        console.print("[yellow]a tournament needs at least 2 contenders.[/yellow]")
        raise typer.Exit(2)
    judge_spec = parse_model_spec(judge) if judge else None
    console.print(f"[dim]🏟 {len(specs)} contenders enter the bracket…[/dim]")

    def _on_match(m):
        if m.b is None:
            console.print(f"  [dim]{m.a} — bye →[/dim] [green]{m.winner}[/green]")
        else:
            console.print(f"  {m.a}  vs  {m.b}   →  [green]★ {m.winner}[/green]")

    with console.status("[dim] running matchups…[/dim]", spinner="dots"):
        result = run_tournament(config, question, specs, judge_spec=judge_spec, on_match=_on_match)
    console.print(f"\n[bold #2dd4bf]🏆 champion: {result.champion}[/bold #2dd4bf] "
                  f"[dim]({len(result.rounds)} round(s))[/dim]")


# ---------- migrate (guided large refactor) ----------

@dev_app.command()
def migrate(
    change: str = typer.Argument(..., help="The migration, e.g. \"replace requests with httpx\"."),
    match: str = typer.Option(None, "--match", help="Only files containing this substring (e.g. 'requests')."),
    glob: str = typer.Option("*", "--glob", help="Only files matching this glob (e.g. '*.py')."),
    execute: bool = typer.Option(False, "--execute", help="Actually do it (default: dry-run plan)."),
    duel: str = typer.Option(None, "--duel", help="Red-team each patch with a rival provider."),
    budget: float = typer.Option(None, "--budget", help="USD spend cap."),
    limit: int = typer.Option(30, "--limit", help="Max files."),
    root: Path = typer.Option(Path("."), "--root", help="Repo to migrate."),
) -> None:
    """🛠  Guided large migration — find every file that needs a sweeping change,
    work each ONE in isolation (test-gated), and leave a reviewable patch per file.
    Dry-run by default; --execute to do it. `ronin patches` to review/apply.
    """
    from .migrate import find_targets, to_tasks

    targets = find_targets(root, glob=glob, contains=match, limit=limit)
    if not targets:
        console.print(f"[dim]no files matched (glob={glob}"
                      f"{', contains=' + match if match else ''}).[/dim]")
        return
    console.print(f"[#7aa2f7]🛠 migration plan — {len(targets)} file(s)[/#7aa2f7] [dim]{change}[/dim]")
    for t in targets[:20]:
        hits = f" [dim]({t.hits}×)[/dim]" if t.hits else ""
        console.print(f"  [cyan]{t.path}[/cyan]{hits}")
    if not execute:
        console.print("\n[dim]dry-run — add [bold]--execute[/bold] to work the migration into patches.[/dim]")
        return
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .consensus import parse_model_spec
    duel_spec = parse_model_spec(duel) if duel else None
    from .nightshift import run_nightshift, summarize
    from .migrate import to_tasks
    tasks = to_tasks(change, targets)
    console.print("[dim]working each file (isolated worktrees, test-gated)…[/dim]\n")
    results = run_nightshift(config, root, console, tasks, execute=True,
                             duel_against=duel_spec, budget=budget)
    counts = summarize(results)
    console.print(f"\n[bold]🛠 migration[/bold] — [green]{counts['patched']} patch(es)[/green], "
                  f"{counts['failed-tests']} failed, {counts['no-change']} no-change")
    console.print("[dim]review with [bold]ronin patches[/bold] · apply with "
                  "[bold]ronin patches --apply --clean[/bold].[/dim]")


# ---------- pair (ambient co-pilot) ----------

@dev_app.command()
def pair(
    interval: float = typer.Option(2.0, "--interval", help="Poll interval (seconds)."),
    root: Path = typer.Option(Path("."), "--root", help="Directory to watch."),
) -> None:
    """👥 Ambient pair programmer — ronin watches your saves and chimes in with a
    short suggestion on each change (a bug, a cleaner way, a missing test). Ctrl+C to stop.
    """
    config = load_config()
    if not config.has_provider_auth():
        console.print(f"[red]✗[/red] No credentials for [bold]{config.provider}[/bold].")
        raise typer.Exit(2)
    from .pair import run_pair
    run_pair(config, root, console, interval=interval)


# ---------- archaeology (why is this code here) ----------

@dev_app.command()
def archaeology(
    file: str = typer.Argument(..., help="The file to investigate."),
    limit: int = typer.Option(25, "--limit", help="How many commits of history to read."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🏺 Why is this code the way it is? ronin reconstructs the file's story from
    git history and explains what each significant change was for + any gotchas.
    """
    from .archaeology import file_history, history_brief

    commits = file_history(root, file, limit=limit)
    if not commits:
        console.print(f"[yellow]no git history for[/yellow] [bold]{file}[/bold] [dim](new file, or not tracked?)[/dim]")
        raise typer.Exit(1)
    console.print(f"[#6b7089]🏺 {len(commits)} commit(s) shaped[/#6b7089] [bold]{file}[/bold]")
    for c in commits[:12]:
        console.print(f"  [dim]{c['date']}[/dim] [cyan]{c['short']}[/cyan] "
                      f"[#6b7089]{c['author'][:16]}[/#6b7089]  {c['subject'][:60]}")

    config = load_config()
    if not config.has_provider_auth():
        console.print("[dim]set a provider for the AI history analysis.[/dim]")
        return
    from .code_mode import run_code_agent
    console.print("\n[#6b7089]reading the story…[/#6b7089]")
    run_code_agent(
        config,
        f"Read @{file} and its git history below. Explain its archaeology concisely: "
        "the major phases it went through, what each significant change solved, and "
        "any gotchas or load-bearing decisions the history reveals.\n\n"
        f"History (newest first):\n{history_brief(commits)}",
        root=root, console=console, read_only=True, include_image_tool=False, max_iterations=8)


# ---------- perf (benchmark + analyze) ----------

@dev_app.command()
def perf(
    command: list[str] = typer.Argument(..., help="The command to benchmark, e.g. \"pytest -q\"."),
    runs: int = typer.Option(5, "--runs", help="Timed runs (after a warmup)."),
    warmup: int = typer.Option(1, "--warmup", help="Discarded warmup runs."),
    timeout: int = typer.Option(300, "--timeout", help="Per-run timeout in seconds."),
    json_out: Optional[Path] = typer.Option(None, "--json-out", help="Write a structured benchmark report."),
    baseline: Optional[Path] = typer.Option(None, "--baseline", help="Compare this run with a saved report."),
    max_regression_percent: float = typer.Option(
        10.0, "--max-regression-percent", help="Fail when median latency rises above this percent.",
    ),
    analyze: bool = typer.Option(False, "--analyze", help="Have ronin suggest where the time goes."),
    root: Path = typer.Option(Path("."), "--root", help="Working directory."),
) -> None:
    """Benchmark a command, optionally save JSON, compare a baseline, or analyze it."""
    from .perf import compare_reports, load_report, run_benchmark, save_report

    cmd = " ".join(command)
    console.print(f"[#7aa2f7]⏱ benchmarking[/#7aa2f7] [bold]{cmd}[/bold] "
                  f"[dim]({runs} timed + {warmup} warmup)[/dim]")
    with console.status("[dim] running…[/dim]", spinner="dots"):
        report = run_benchmark(cmd, runs=runs, warmup=warmup, timeout=timeout, root=root)
    r = report.summary()
    if r["runs"] == 0:
        console.print("[yellow]no completed timed runs.[/yellow]")
        raise typer.Exit(1)
    console.print(f"  mean   [bold]{r['mean']:.3f}s[/bold]  [dim]± {r['stdev']:.3f}[/dim]")
    console.print(f"  median {r['median']:.3f}s   p95 {r['p95']:.3f}s   "
                  f"min {r['min']:.3f}s   max {r['max']:.3f}s")
    if r["failures"]:
        console.print(f"  [yellow]{r['failures']} run(s) exited non-zero[/yellow]")
    if json_out is not None:
        try:
            saved = save_report(report, json_out)
        except OSError as exc:
            console.print(f"[red]could not save benchmark report:[/red] {exc}")
            raise typer.Exit(2)
        console.print(f"  [dim]report:[/dim] {saved}")

    comparison = None
    if baseline is not None:
        try:
            comparison = compare_reports(
                load_report(baseline), report,
                max_regression_percent=max_regression_percent,
            )
        except ValueError as exc:
            console.print(f"[red]could not compare baseline:[/red] {exc}")
            raise typer.Exit(2)
        color = {
            "improved": "green", "unchanged": "yellow", "regressed": "red", "insufficient": "yellow",
        }[comparison.status]
        console.print(f"  [{color}]baseline {comparison.status}[/{color}]: {comparison.reason}")
    if analyze:
        config = load_config()
        if not config.has_provider_auth():
            console.print("[dim]set a provider to enable analysis.[/dim]")
        else:
            from .code_mode import run_code_agent
            console.print("\n[#6b7089]analyzing…[/#6b7089]")
            run_code_agent(
                config,
                f"`{cmd}` runs in ~{r['mean']:.3f}s (median {r['median']:.3f}s). Explore the "
                "code it exercises and suggest the 1-3 highest-leverage speedups. Be specific.",
                root=root, console=console, read_only=True, include_image_tool=False, max_iterations=10)
    if comparison is not None and comparison.status == "regressed":
        raise typer.Exit(1)


# ---------- debate (multi-round cross-vendor argument) ----------

@util_app.command()
def debate(
    question: str = typer.Argument(..., help="The question to debate."),
    models: str = typer.Option(..., "--models", "-m", help="Panelists, e.g. 'anthropic,gemini,cerebras'."),
    rounds: int = typer.Option(2, "--rounds", help="Debate rounds (≥2 lets them critique each other)."),
    judge: str = typer.Option(None, "--judge", help="provider[:model] to synthesize (default: current)."),
) -> None:
    """🥊 Several models DEBATE a question across rounds — each sees the others'
    answers, critiques them, and refines its own — then a judge synthesizes the
    converged answer. Cross-examination across vendors, not a one-shot vote.
    """
    from .consensus import parse_model_spec
    from .debate import run_debate

    config = load_config()
    specs = [parse_model_spec(s) for s in models.split(",") if s.strip()]
    if len(specs) < 2:
        console.print("[yellow]a debate needs at least 2 panelists[/yellow] — e.g. [cyan]-m anthropic,gemini[/cyan]")
        raise typer.Exit(2)
    judge_spec = parse_model_spec(judge) if judge else None
    labels = ", ".join(f"{s['provider']}{':' + s['model'] if s.get('model') else ''}" for s in specs)
    console.print(f"[dim]🥊 {len(specs)} panelists · {rounds} rounds — {labels}[/dim]")

    def _on_round(r, panelists):
        console.print(f"\n[bold #7aa2f7]── round {r + 1} ──[/bold #7aa2f7]")
        for p in panelists:
            if p.rounds and p.rounds[-1].strip():
                console.print(f"[bold]{p.label}[/bold]")
                console.print(f"  [#c0caf5]{p.rounds[-1][:500]}[/#c0caf5]")

    result = run_debate(config, question, specs, rounds=rounds, judge_spec=judge_spec, on_round=_on_round)
    from rich.panel import Panel
    console.print(Panel(result.synthesis or "(no synthesis)",
                        title="[bold #2dd4bf]⚖ synthesis[/bold #2dd4bf]", border_style="#2dd4bf", padding=(0, 1)))


# ---------- bisect (autonomous regression hunt) ----------

@dev_app.command()
def bisect(
    command: list[str] = typer.Argument(..., help="The command that FAILS now but passed before, e.g. \"pytest -k test_login\"."),
    good: str = typer.Option(None, "--good", help="A known-good ref (default: last tag, else HEAD~20)."),
    bad: str = typer.Option("HEAD", "--bad", help="A known-bad ref (default: HEAD)."),
    analyze: bool = typer.Option(True, "--analyze/--no-analyze", help="Have ronin explain the culprit diff."),
    root: Path = typer.Option(Path("."), "--root", help="Repo to bisect."),
) -> None:
    """🔬 Find the commit that broke something — automatically. ronin drives
    git bisect (in an isolated worktree, your checkout untouched) using your
    command as the oracle, then explains the culprit's diff and suggests a fix.
    """
    from .bisect import default_good, run_bisect

    cmd = " ".join(command)
    g = good or default_good(root)
    if not g:
        console.print("[yellow]couldn't find a known-good ref — pass --good <ref>.[/yellow]")
        raise typer.Exit(2)
    console.print(f"[#7aa2f7]🔬 bisecting[/#7aa2f7] [dim]{g[:12]} … {bad} · oracle: {cmd}[/dim]")
    with console.status("[dim] running git bisect (isolated worktree)…[/dim]", spinner="dots"):
        res = run_bisect(root, cmd, good=g, bad=bad)
    if not res.culprit:
        console.print(f"[yellow]no culprit found[/yellow] [dim]({res.note})[/dim]")
        raise typer.Exit(1)
    console.print(f"[bold #f7768e]✗ first bad commit:[/bold #f7768e] [bold]{res.subject}[/bold]")

    if analyze:
        config = load_config()
        if not config.has_provider_auth():
            console.print(f"[dim]commit {res.culprit[:12]} — set a provider to get an AI analysis.[/dim]")
            return
        from .code_mode import run_code_agent
        console.print("[#6b7089]analyzing the culprit…[/#6b7089]")
        run_code_agent(
            config,
            f"This commit introduced a regression that makes `{cmd}` fail. Read the "
            f"diff and explain, concisely: (1) what change most likely caused it, and "
            f"(2) a specific fix. Use read-only tools to check the current code if needed.\n\n"
            f"```diff\n{res.diff[:12000]}\n```",
            root=root, console=console, read_only=True, include_image_tool=False, max_iterations=8,
        )


# ---------- status (mission-control dashboard) ----------

@util_app.command()
def status(root: Path = typer.Option(Path("."), "--root", help="Repo to summarize.")) -> None:
    """🛰  Mission control — one view of every ronin system: Cost-Router savings,
    the Dojo leaderboard, what the self-tuning router has learned, your sessions,
    git state, and the last Nightshift.
    """
    from .dashboard import gather, render
    config = load_config()
    render(console, gather(root, config))


# ---------- leaderboard (dojo standings) ----------

@util_app.command()
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

@util_app.command()
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

@util_app.command()
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

@util_app.command()
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

@util_app.command()
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

@util_app.command()
def agent(
    goal: list[str] = typer.Argument(..., help="The goal for the agent to pursue. Quote multi-word goals."),
    confirm: bool = typer.Option(False, "--confirm", help="Pause for y/N approval before each tool call (Cline-style)."),
    max_steps: int = typer.Option(15, "--max-steps", help="Iteration cap for the autonomous loop."),
    raw: bool = typer.Option(False, "--raw", help="Plain output."),
    faithfulness: str = typer.Option(
        None, "--faithfulness",
        help="Grounding harness: off | warn | gate. Scores the answer against the "
             "sources the agent read. Defaults to the config setting.",
    ),
) -> None:
    """Autonomous multi-step agent: give it a goal, it chains tool calls until done.

    Unlike `ronin ask` (one-shot) and `ronin chat` (turn-by-turn), `agent` runs an
    autonomous loop, narrating each step live. With --confirm it pauses for your
    approval before every tool call.

    --faithfulness off|warn|gate runs the grounding harness on the final answer:
    warn surfaces a faithfulness score and any ungrounded claims / hallucinated
    symbols; gate additionally holds an ungrounded answer for your confirmation.
    """
    config = load_config()
    from .agent_mode import has_real_key, run_agent

    if faithfulness is not None and faithfulness.strip().lower() not in ("off", "warn", "gate"):
        console.print("[red]✗[/red] --faithfulness must be one of: off, warn, gate.")
        raise typer.Exit(2)

    if not has_real_key(config):
        console.print(
            f"[red]✗[/red] Agent mode needs a real LLM — the offline demo brain can't reason multi-step. "
            f"Set credentials for provider [bold]{config.provider}[/bold] (e.g. [bold]export ANTHROPIC_API_KEY=...[/bold])."
        )
        raise typer.Exit(2)

    text = " ".join(goal)
    console.print(Panel.fit(f"[bold]Goal:[/bold] {text}", border_style="cyan", title="ronin agent"))

    fmode = faithfulness.strip().lower() if faithfulness is not None else None
    result = run_agent(
        config, text, console=console, confirm=confirm,
        max_iterations=max_steps, faithfulness=fmode,
    )

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

    # Faithfulness gate: in gate mode, an ungrounded / abstaining answer is held
    # for the user to accept or reject (the warning was already rendered by the
    # hook inside run_agent).
    outcome = getattr(result, "faithfulness", None)
    if outcome is not None and outcome.should_gate:
        console.print(
            "[yellow]![/yellow] this answer is not well grounded in the sources the "
            "agent read. Accept it anyway? [y/N] ", end="",
        )
        try:
            ok = input().strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            ok = False
        if not ok:
            console.print("[red]✗[/red] answer held back as ungrounded.")
            raise typer.Exit(3)

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
    context_window: Optional[int] = typer.Option(None, "--context-window", min=4_096, max=2_000_000, help="Temporary context-window override in tokens; does not change config."),
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
    if context_window is not None:
        config.context_window = context_window
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

@util_app.command()
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

@dev_app.command()
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


@dev_app.command(name="fix")
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

@dev_app.command()
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


@dev_app.command()
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

@util_app.command()
def version() -> None:
    """Print the ronin version.

    For a git checkout this also shows the short commit sha and branch, e.g.
    "ronin 1.0.0-rc.3 (a1b2c3d, main)". For a wheel/pip install it prints just the
    version. Git context is computed at runtime and git errors are swallowed.
    """
    from .selfupdate import version_line

    console.print(version_line())


# ---------- update ----------

def _post_update_health_check() -> bool:
    """Live-check the configured provider/model after an update, reusing the same
    end-to-end probe as ``ronin doctor --check``. Prints the result (and the
    exact fix on failure) and returns True iff the provider answers.

    A ronin upgrade can ship a new default model or provider handling, so a
    config that worked before the pull can silently 404 on the next run — this
    surfaces that immediately instead of at the next real command."""
    config = load_config()
    with console.status("[dim]post-update health check: live-checking provider…[/dim]", spinner="dots"):
        live = _provider_live_check(config)
    console.print(f"health check ({config.provider}/{config.resolved_model()}): {live.status}")
    if live.remedy and not live.ok:
        console.print(live.remedy)
    return live.ok


@app.command()
def update(
    check: bool = typer.Option(
        False, "--check",
        help="Only report whether updates are available; do not change anything.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Update even when there are uncommitted local changes (they are reset).",
    ),
    doctor: bool = typer.Option(
        False, "--doctor",
        help="After updating, live-check the provider/model (like `ronin doctor --check`) so a config broken by the upgrade surfaces now, not on the next run.",
    ),
) -> None:
    """Update this ronin install in place from origin/main.

    Works only for a git checkout. It fetches origin, hard-resets to
    origin/main, and re-runs `uv sync`. Use --check to see if there is anything
    to pull without touching the working tree. Add --doctor to live-check the
    provider/model once the update lands.
    """
    import subprocess

    from .selfupdate import find_repo_root, git_branch, git_short_sha, version_line

    root = find_repo_root()
    if root is None:
        console.print(
            "[yellow]This ronin install is not a git checkout, so `ronin update` "
            "cannot self-update it.[/yellow]\n"
            "You likely installed from a wheel (pip/uv/pipx). To get the latest "
            "version, reinstall the package, e.g.:\n"
            "  [cyan]uv tool upgrade ronin-cli[/cyan]   or   [cyan]pipx upgrade ronin-cli[/cyan]\n"
            "Or clone the repo and install from source:\n"
            "  [cyan]git clone https://github.com/rohithkandula19/Ronin.git[/cyan]"
        )
        raise typer.Exit(code=0)

    def _run(args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
        """Run git/uv. Streams to the terminal unless capture is requested."""
        return subprocess.run(
            args,
            capture_output=capture,
            text=True,
        )

    # Fetch first — needed by both --check and a real update.
    console.print(f"[dim]fetching origin in {root} ...[/dim]")
    fetched = _run(["git", "-C", str(root), "fetch", "origin"])
    if fetched.returncode != 0:
        console.print("[red]git fetch failed.[/red]")
        raise typer.Exit(code=1)

    local = git_short_sha(root)
    remote_full = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "origin/main"],
        capture_output=True, text=True,
    )
    if remote_full.returncode != 0:
        console.print("[red]could not resolve origin/main.[/red]")
        raise typer.Exit(code=1)
    remote_short = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--short", "origin/main"],
        capture_output=True, text=True,
    ).stdout.strip()
    local_full = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()

    up_to_date = local_full == remote_full.stdout.strip()

    if check:
        if up_to_date:
            console.print(f"[green]up to date[/green] ({local} on {git_branch(root)}).")
        else:
            console.print(
                f"[yellow]update available[/yellow]: {local} -> {remote_short} "
                f"(origin/main). Run [cyan]ronin update[/cyan] to apply."
            )
        # --check never mutates the working tree.
        raise typer.Exit(code=0)

    if up_to_date:
        console.print(f"[green]already up to date[/green] ({local} on {git_branch(root)}).")
        if doctor and not _post_update_health_check():
            raise typer.Exit(code=1)
        raise typer.Exit(code=0)

    # Refuse to clobber uncommitted work unless --force.
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True, text=True,
    ).stdout.strip()
    if dirty and not force:
        console.print(
            "[red]aborting:[/red] you have uncommitted local changes.\n"
            "Commit or stash them, or re-run with [cyan]--force[/cyan] to discard them."
        )
        raise typer.Exit(code=1)

    old_sha = local

    console.print(f"[dim]resetting {root} to origin/main ...[/dim]")
    reset = _run(["git", "-C", str(root), "reset", "--hard", "origin/main"])
    if reset.returncode != 0:
        console.print("[red]git reset --hard origin/main failed.[/red]")
        raise typer.Exit(code=1)

    console.print("[dim]running uv sync ...[/dim]")
    synced = _run(["uv", "sync", "--project", str(root), "--all-packages"])
    if synced.returncode != 0:
        console.print(
            "[red]uv sync failed.[/red] The checkout was updated but dependencies "
            "may be out of date. Re-run [cyan]uv sync --all-packages[/cyan] in the repo."
        )
        raise typer.Exit(code=1)

    new_sha = git_short_sha(root)
    console.print(f"[green]updated[/green]: {old_sha} -> {new_sha}")
    console.print(version_line())

    if doctor and not _post_update_health_check():
        raise typer.Exit(code=1)


@app.command()
def memory(
    add: str = typer.Option(None, "--add", help="Add a fact to long-term memory."),
    recall_query: str = typer.Option(None, "--recall", help="Show the facts most relevant to a query."),
    forget_match: str = typer.Option(None, "--forget", help="Forget facts matching a phrase."),
    clear: bool = typer.Option(False, "--clear", help="Forget everything."),
) -> None:
    """Show (or edit) what ronin remembers about you across sessions.

    Memory is persistent and user-global (~/.ronin/memory.json): ronin recalls
    these facts in every future session. The agent also saves facts itself via
    its `remember` tool when you share durable info.
    """
    from .memory_store import add_memory, forget, forget_all, load_memories, recall

    if clear:
        n = forget_all()
        console.print(f"[green]✓[/green] forgot {n} memory item(s).")
        return
    if forget_match:
        n = forget(forget_match)
        console.print(f"[green]✓[/green] forgot {n} memory item(s) matching {forget_match!r}."
                      if n else f"[dim]nothing matched {forget_match!r}[/dim]")
        return
    if recall_query:
        hits = recall(recall_query)
        if not hits:
            console.print(f"[dim]nothing relevant to {recall_query!r}[/dim]")
        else:
            for f in hits:
                console.print(f"[#2dd4bf]•[/#2dd4bf] {f}")
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


@util_app.command()
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


@util_app.command()
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


@util_app.command()
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


@util_app.command()
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


@util_app.command()
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


@dev_app.command()
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


# ---------- explain-error (parse a trace, get cause + fix) ----------

@dev_app.command(name="explain-error")
def explain_error(
    trace: list[str] = typer.Argument(None, help="The error/stack trace. Omit to read piped stdin."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root the cited paths are relative to."),
    context: int = typer.Option(3, "--context", "-C", help="Source lines of context around each cited line."),
) -> None:
    """Paste a stack trace, get a root-cause + fix sketch. Read-only.

    Takes the trace as an argument or piped stdin (like ronin ask). Parses the
    referenced files/lines (Python, Node, Go, Rust), pulls those source lines from
    the repo as context, and asks the model for a plain-English cause + a concrete
    fix. With no provider key it still prints the parsed frames + cited source.

    Examples:
      ronin explain-error "Traceback ... ValueError: bad"
      pytest 2>&1 | ronin explain-error
    """
    from .explain_error import (
        build_prompt,
        extract_error_message,
        format_frames,
        gather_snippets,
        parse_trace,
    )

    text = " ".join(trace) if trace else ""
    # Pipe support mirrors `ronin ask`: fold piped stdin in as the trace.
    if not sys.stdin.isatty():
        try:
            piped = sys.stdin.read().strip()
        except Exception:  # noqa: BLE001
            piped = ""
        if piped:
            text = f"{text}\n{piped}" if text else piped
    if not text.strip():
        console.print("[red]✗[/red] nothing to explain — give a trace or pipe input "
                      "([dim]pytest 2>&1 | ronin explain-error[/dim]).")
        raise typer.Exit(2)

    frames = parse_trace(text)
    error_message = extract_error_message(text)
    snippets = gather_snippets(frames, root, context=context)

    if error_message:
        console.print(f"[bold red]error:[/bold red] {error_message}")
    console.print("[bold]frames[/bold]")
    console.print(format_frames(frames))
    for s in snippets:
        if s.found:
            loc = f"{s.frame.file}:{s.frame.line}"
            console.print(Panel(s.snippet, title=loc, border_style="#6b7089", padding=(0, 1)))

    config = load_config()
    if not config.has_provider_auth():
        console.print("[dim]no provider key — showing parsed frames + cited source only. "
                      "Run [bold]ronin set-key[/bold] for a cause + fix.[/dim]")
        return

    prompt = build_prompt(error_message, snippets, text)
    try:
        from ronin_agent_patterns import Message

        from .runner import build_single_provider
        provider = build_single_provider(config)
        with console.status("[dim] analyzing…[/dim]", spinner="dots"):
            resp = provider.complete(
                system="You are a senior engineer who diagnoses errors precisely. "
                       "Give the root cause, then a concrete fix.",
                messages=[Message(role="user", content=prompt)],
                tools=[], max_tokens=600)
        answer = (resp.text or "").strip()
    except Exception as e:  # noqa: BLE001 — degrade to the offline view
        console.print(f"[dim]model call failed ({e}); parsed frames + source shown above.[/dim]")
        return

    from rich.markdown import Markdown
    console.print()
    console.print(Panel(Markdown(answer) if answer else "[dim](no answer)[/dim]",
                        title="cause + fix", border_style="green", padding=(1, 2)))


# ---------- guard (intent / scope-creep gate) ----------

@dev_app.command()
def guard(
    intent: str = typer.Option(None, "--intent", help="What this change is meant to do — enables an LLM scope-creep check."),
    analyze: bool = typer.Option(True, "--analyze/--no-analyze", help="With --intent, flag files that drift from it."),
    root: Path = typer.Option(Path("."), "--root", help="Repo to check."),
) -> None:
    """🛡  Pre-commit guard — scan your working diff for debug/secret leftovers
    (stray breakpoints, console.log, merge markers, AWS keys) and, with --intent,
    flag files that drift from the task. Exits non-zero on high-severity findings,
    so it drops straight into a pre-commit hook or CI.
    """
    from .guard import collect_diff, run_guard

    res = run_guard(root)
    if res.note:
        console.print(f"[dim]{res.note}[/dim]")
        raise typer.Exit(0)
    if not res.findings:
        console.print(f"[green]✓ clean[/green] [dim]{len(res.changed_files)} file(s) changed, no leftovers[/dim]")
    else:
        table = Table(box=box.SIMPLE)
        table.add_column("sev", no_wrap=True)
        table.add_column("kind", style="bold", no_wrap=True)
        table.add_column("file", style="cyan", no_wrap=True)
        table.add_column("line", overflow="fold")
        for f in res.findings:
            color = "red" if f.severity == "high" else "yellow"
            table.add_row(f"[{color}]{f.severity}[/{color}]", f.kind, f.file, f.text)
        console.print(table)

    if intent and analyze:
        config = load_config()
        if config.has_provider_auth():
            from .code_mode import run_code_agent
            console.print("[#6b7089]checking scope against intent…[/#6b7089]")
            run_code_agent(
                config,
                f"The intended change was: {intent!r}. Files touched: {res.changed_files}. "
                f"Read the diff and name, concisely, any file or hunk that does NOT serve that "
                f"intent (scope creep) — or reply 'no scope creep'.\n\n"
                f"```diff\n{collect_diff(root)[:12000]}\n```",
                root=root, console=console, read_only=True, include_image_tool=False, max_iterations=6,
            )
    if res.high:
        raise typer.Exit(1)


# ---------- flake (flaky-test hunter) ----------

@dev_app.command()
def flake(
    command: list[str] = typer.Argument(..., help='Test command to repeat, e.g. "pytest -q -k login".'),
    runs: int = typer.Option(5, "--runs", "-n", help="How many times to run it."),
    root: Path = typer.Option(Path("."), "--root", help="Where to run."),
) -> None:
    """🎲 Flaky-test hunter — run your test command N times and report which tests
    are non-deterministic (green on one run, red on the next), ranked by how often
    they flip. A single run can't tell flaky from stable; this can.
    """
    from .flake import run_flake

    cmd = " ".join(command)
    console.print(f"[#7aa2f7]🎲 running[/#7aa2f7] [dim]{cmd} · {runs}×[/dim]")
    with console.status("[dim] repeating test runs…[/dim]", spinner="dots"):
        res = run_flake(root, cmd, runs=runs)
    if res.note:
        console.print(f"[yellow]{res.verdict}[/yellow] [dim]({res.note})[/dim]")
        raise typer.Exit(2)
    bar = "".join("[green]✓[/green]" if ok else "[red]✗[/red]" for ok in res.outcomes)
    console.print(f"runs: {bar}  [dim]({res.verdict})[/dim]")
    if res.verdict == "flaky":
        table = Table(box=box.SIMPLE)
        table.add_column("flaky test", style="cyan")
        table.add_column("failed", justify="right")
        for t, c in res.flaky:
            table.add_row(t, f"{c}/{res.runs}")
        console.print(table)
        raise typer.Exit(1)
    if res.verdict == "stable-pass":
        console.print("[green]✓ green every run — no flakiness detected[/green]")
    else:
        console.print("[red]✗ stable failure — not flaky, just broken[/red]")
        raise typer.Exit(1)


# ---------- mutants (mutation-testing gate) ----------

@dev_app.command()
def mutants(
    target: str = typer.Argument(..., help="Source file to mutate, e.g. parser.py."),
    test: str = typer.Option("python -m pytest -q", "--test", help="Test command that SHOULD catch mutations."),
    max_mutants: int = typer.Option(40, "--max", help="Cap on mutants tried."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🧬 Mutation testing — inject tiny faults into TARGET (== → !=, and → or, …),
    run your suite against each, and list the mutants that SURVIVED: every survivor
    is a bug your tests would have missed. Coverage says lines ran; this says they're
    actually checked. The original file is always restored.
    """
    from .mutants import run_mutants

    console.print(f"[#7aa2f7]🧬 mutating[/#7aa2f7] [dim]{target} · oracle: {test}[/dim]")
    with console.status("[dim] running the suite per mutant…[/dim]", spinner="dots"):
        res = run_mutants(root, target, test, max_mutants=max_mutants)
    if res.note:
        console.print(f"[yellow]{res.note}[/yellow]")
        raise typer.Exit(2)
    pct = round(res.score * 100)
    color = "green" if res.score >= 0.8 else ("yellow" if res.score >= 0.5 else "red")
    console.print(f"mutation score: [{color}]{res.killed}/{res.total} killed ({pct}%)[/{color}]")
    if res.survivors:
        table = Table(box=box.SIMPLE, title="survivors — tests didn't catch these")
        table.add_column("line", justify="right", no_wrap=True)
        table.add_column("mutation", style="bold")
        for m in res.survivors:
            table.add_row(str(m.lineno), m.label)
        console.print(table)
        raise typer.Exit(1)
    console.print("[green]✓ every mutant killed — strong tests[/green]")


# ---------- radius (blast radius + impact-aware tests) ----------

@dev_app.command()
def radius(
    run: bool = typer.Option(False, "--run", help="Also run the affected test files with pytest."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🌐 Blast radius — from your uncommitted changes, walk the import graph
    backwards to every module that depends on what you touched, and surface the
    test modules in that radius so you can run only what matters.
    """
    from .radius import run_radius

    res = run_radius(root)
    if res.note:
        console.print(f"[dim]{res.note}[/dim]")
        raise typer.Exit(0)
    console.print(f"[bold]changed:[/bold] {', '.join(res.changed)}")
    extra = f" — {', '.join(res.radius)}" if res.radius else ""
    console.print(f"[bold]blast radius:[/bold] {len(res.radius)} module(s){extra}")
    if res.affected_tests:
        console.print(f"[bold]affected tests:[/bold] {', '.join(res.affected_tests)}")
    else:
        console.print("[yellow]no test modules in the blast radius — this change may be untested[/yellow]")
    if run and res.affected_tests:
        import subprocess
        console.print("[#6b7089]running affected tests…[/#6b7089]")
        proc = subprocess.run(["python", "-m", "pytest", "-q", *res.affected_tests],
                              cwd=str(Path(root).resolve()))
        raise typer.Exit(proc.returncode)


# ---------- graph / architecture (Phase 6 repo cognition) ----------

@dev_app.command()
def graph(
    path: Path = typer.Argument(Path("."), help="Repository root to analyze."),
    format: str = typer.Option("text", "--format", "-f", help="text | json | mermaid"),
    cycles_only: bool = typer.Option(False, "--cycles", help="Only report circular imports."),
    strict: bool = typer.Option(False, "--strict", help="Exit non-zero if any circular import exists (CI gate)."),
    max_nodes: int = typer.Option(60, "--max-nodes", help="Max nodes in a mermaid diagram."),
) -> None:
    """🕸  Build the repository's module-level import graph (stdlib AST, no LLM).

    Maps intra-repo imports into a dependency graph, then reports fan-in/fan-out,
    the most depended-on modules, and circular imports. Render as text, JSON, or a
    Mermaid diagram. ``--strict`` exits non-zero on any cycle so CI can gate on it.
    """
    from . import repo_graph as rg

    g = rg.build_import_graph(path)
    if not g.modules():
        console.print("[#e0af68]no Python modules found[/#e0af68]")
        raise typer.Exit(2)

    if cycles_only:
        cyc = rg.find_cycles(g)
        if not cyc:
            console.print("[#9ece6a]no circular imports[/#9ece6a]")
        else:
            console.print(f"[#f7768e]{len(cyc)} circular import group(s):[/#f7768e]")
            for c in cyc:
                console.print("  " + " → ".join(c) + " → " + c[0])
        raise typer.Exit(1 if (cyc and strict) else 0)

    fmt = format.lower()
    if fmt == "json":
        console.print_json(rg.to_json(g))
    elif fmt == "mermaid":
        console.print(rg.to_mermaid(g, max_nodes=max_nodes))
    else:
        console.print(rg.to_text(g))
    if strict and rg.find_cycles(g):
        raise typer.Exit(1)


@dev_app.command()
def architecture(
    path: Path = typer.Argument(Path("."), help="Repository root to analyze."),
    format: str = typer.Option("text", "--format", "-f", help="text | json | mermaid"),
) -> None:
    """🏛  Architecture overview: detected stack + package structure + dependency
    shape (stdlib AST, no LLM). ``--format mermaid`` emits a package-level diagram.
    """
    from . import repo_graph as rg

    g = rg.build_import_graph(path)
    stack = rg.detect_stack(path, g)
    pkgs = rg.top_packages(g)
    m = rg.metrics(g)

    if format.lower() == "json":
        console.print_json(data={
            "stack": stack, "packages": pkgs,
            "modules": m["modules"], "edges": m["edges"], "cycles": m["cycles"],
        })
        return
    if format.lower() == "mermaid":
        console.print(rg.to_mermaid(g))
        return

    console.print("[bold]stack[/bold]")
    console.print("  languages/build: " + (", ".join(stack["manifests"]) or "—"))
    console.print("  frameworks:      " + (", ".join(stack["frameworks"]) or "—"))
    console.print("\n[bold]packages[/bold] (modules each)")
    for name, n in list(pkgs.items())[:15]:
        console.print(f"  {n:>4}  {name}")
    console.print(f"\n[bold]shape[/bold]  modules {m['modules']} · edges {m['edges']} · cycles {m['cycles']}")
    if m["cycles"]:
        console.print("  [#e0af68]run `ronin graph --cycles` to see the circular imports[/#e0af68]")


# ---------- remember / forget / timeline (Phase 6 memory surface) ----------

@util_app.command()
def remember(
    fact: list[str] = typer.Argument(..., help='The fact, e.g. `ronin remember I use Groq`.'),
) -> None:
    """🧠 Remember a durable fact about you (user-global, across every session).

    Refuses anything that looks like a secret: memories are injected into the
    agent's system prompt on every future run, so a stored key would be re-sent
    to the provider forever. ronin never stores keys.
    """
    from .memory_store import add_memory, secret_labels

    text = " ".join(fact).strip()
    if not text:
        console.print("[#f7768e]nothing to remember[/#f7768e]")
        raise typer.Exit(2)

    labels = secret_labels(text)
    if labels:
        kinds = ", ".join(sorted(set(labels)))
        console.print(f"[#f7768e]refused — that looks like a secret ({kinds}).[/#f7768e]")
        console.print("[dim]memory is injected into every future prompt; ronin never stores keys.[/dim]")
        raise typer.Exit(1)

    if add_memory(text):
        console.print(f"[#9ece6a]✓[/#9ece6a] remembered: {text}")
    else:
        console.print("[dim]already known — nothing added[/dim]")


@util_app.command()
def forget(
    pattern: list[str] = typer.Argument(None, help="Forget facts matching this phrase."),
    forget_everything: bool = typer.Option(False, "--all", help="Forget everything."),
) -> None:
    """🧽 Forget remembered facts matching a phrase (or ``--all``)."""
    from .memory_store import forget as _forget_matching
    from .memory_store import forget_all

    if forget_everything:
        n = forget_all()
        console.print(f"[#9ece6a]✓[/#9ece6a] forgot {n} fact(s).")
        return

    text = " ".join(pattern or []).strip()
    if not text:
        console.print("[#f7768e]give a phrase to forget, or --all[/#f7768e]")
        raise typer.Exit(2)
    n = _forget_matching(text)
    if n:
        console.print(f"[#9ece6a]✓[/#9ece6a] forgot {n} fact(s) matching {text!r}.")
    else:
        console.print(f"[dim]nothing matched {text!r}[/dim]")


@util_app.command()
def timeline(
    limit: int = typer.Option(20, "--limit", "-n", help="How many facts to show (newest last)."),
    format: str = typer.Option("text", "--format", "-f", help="text | json"),
) -> None:
    """🕰  What ronin learned about you, in the order it learned it."""
    from datetime import datetime

    from .memory_store import load_memories

    mems = load_memories()
    if not mems:
        console.print("[dim]nothing remembered yet — try `ronin remember ...`[/dim]")
        return

    recent = mems[-limit:]
    if format.lower() == "json":
        console.print_json(data={"count": len(mems), "facts": recent})
        return

    console.print(f"[bold]memory timeline[/bold] [dim]({len(mems)} fact(s), showing {len(recent)})[/dim]")
    for m in recent:
        ts = m.get("ts")
        when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "—"
        console.print(f"  [dim]{when}[/dim]  {m.get('text', '')}")


# ---------- impact / changed / why (Phase 6 change intelligence) ----------

@dev_app.command()
def impact(
    target: str = typer.Argument(..., help="A module (pkg.mod) or file path to analyze."),
    root: Path = typer.Option(Path("."), "--root", help="Repository root."),
    format: str = typer.Option("text", "--format", "-f", help="text | json"),
) -> None:
    """💥 Blast radius: what transitively imports TARGET — what could break if you
    change it. Reverse-reachability over the import graph (stdlib AST, no LLM).
    """
    from . import repo_graph as rg

    g = rg.build_import_graph(root)
    mod = rg.resolve_target(g, target)
    if mod is None:
        cands = rg.candidates(g, target)
        hint = f" — did you mean: {', '.join(cands[:8])}?" if cands else ""
        console.print(f"[#f7768e]could not resolve '{target}'[/#f7768e]{hint}")
        raise typer.Exit(2)

    direct = sorted(rg.reverse_edges(g).get(mod, set()))
    trans = rg.transitive_dependents(g, mod)
    if format.lower() == "json":
        console.print_json(data={
            "module": mod,
            "direct_dependents": direct,
            "transitive_dependents": sorted(trans),
            "blast_radius": len(trans),
        })
        return
    console.print(f"[bold]{mod}[/bold]  blast radius: {len(trans)} module(s)")
    console.print(f"  direct dependents ({len(direct)}):")
    for d in direct:
        console.print(f"    {d}")
    extra = sorted(trans - set(direct))
    if extra:
        console.print(f"  transitive-only ({len(extra)}):")
        for d in extra[:40]:
            console.print(f"    {d}")
        if len(extra) > 40:
            console.print(f"    … +{len(extra) - 40} more")
    if not trans:
        console.print("  [#9ece6a]nothing imports it — safe to change in isolation[/#9ece6a]")


@dev_app.command()
def why(
    target: str = typer.Argument(..., help="A module (pkg.mod) or file path."),
    root: Path = typer.Option(Path("."), "--root", help="Repository root."),
) -> None:
    """🧭 Explain a module's place: what it imports, what imports it, fan-in/out,
    and whether it sits in a circular-import group.
    """
    from . import repo_graph as rg

    g = rg.build_import_graph(root)
    mod = rg.resolve_target(g, target)
    if mod is None:
        cands = rg.candidates(g, target)
        hint = f" — did you mean: {', '.join(cands[:8])}?" if cands else ""
        console.print(f"[#f7768e]could not resolve '{target}'[/#f7768e]{hint}")
        raise typer.Exit(2)

    deps = sorted(g.edges.get(mod, set()))
    users = sorted(rg.reverse_edges(g).get(mod, set()))
    in_cycle = next((c for c in rg.find_cycles(g) if mod in c), None)
    console.print(f"[bold]{mod}[/bold]  [dim]({g.files.get(mod, '?')})[/dim]")
    console.print(f"  imports {len(deps)} · imported by {len(users)}")
    if deps:
        console.print("  depends on: " + ", ".join(deps[:12]) + (" …" if len(deps) > 12 else ""))
    if users:
        console.print("  used by:    " + ", ".join(users[:12]) + (" …" if len(users) > 12 else ""))
    if in_cycle:
        console.print("  [#e0af68]⟲ in a circular-import group of "
                      f"{len(in_cycle)}: " + " → ".join(in_cycle) + "[/#e0af68]")


@dev_app.command()
def changed(
    ref: str = typer.Argument("HEAD", help="Git ref to diff against (default: HEAD = working tree)."),
    root: Path = typer.Option(Path("."), "--root", help="Repository root."),
    show_impact: bool = typer.Option(False, "--impact", help="Also show each changed module's blast radius."),
) -> None:
    """🔎 Changed Python modules (git diff vs REF) and — with --impact — their
    blast radius. Answers 'what did I touch and what does it affect'.
    """
    import subprocess

    from . import repo_graph as rg

    try:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", ref],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("[#f7768e]not a git repository (or git unavailable)[/#f7768e]")
        raise typer.Exit(2)

    changed_py = [ln for ln in out.splitlines() if ln.endswith(".py")]
    if not changed_py:
        console.print(f"[#9ece6a]no changed Python files vs {ref}[/#9ece6a]")
        return

    g = rg.build_import_graph(root)
    rel_to_mod = {rel: mod for mod, rel in g.files.items()}
    console.print(f"[bold]changed vs {ref}[/bold] ({len(changed_py)} .py file(s))")
    for rel in changed_py:
        mod = rel_to_mod.get(rel)
        if mod is None:
            console.print(f"  {rel}  [dim](not an importable module)[/dim]")
            continue
        if show_impact:
            n = len(rg.transitive_dependents(g, mod))
            console.print(f"  {rel}  [dim]->[/dim] {mod}  [#e0af68](blast radius {n})[/#e0af68]")
        else:
            console.print(f"  {rel}  [dim]->[/dim] {mod}")


# ---------- smell (AST-based code-smell detector) ----------

@dev_app.command()
def smell(
    path: Path = typer.Argument(Path("."), help="A .py file or a directory to scan."),
    max_statements: int = typer.Option(50, "--max-statements", help="Statements per function."),
    max_params: int = typer.Option(6, "--max-params", help="Parameters per function."),
    max_nesting: int = typer.Option(4, "--max-nesting", help="Control-flow nesting depth."),
    max_returns: int = typer.Option(6, "--max-returns", help="Return statements per function."),
    max_locals: int = typer.Option(15, "--max-locals", help="Distinct local names per function."),
    strict: bool = typer.Option(False, "--strict", help="Exit non-zero on any smell (not just HIGH)."),
) -> None:
    """🕵️ Detect AST-based code smells (long functions, deep nesting, broad
    excepts, …). Pure stdlib, no LLM, no network — exits non-zero on HIGH
    smells so CI can gate on it.
    """
    from .smell import HIGH, Thresholds, detect_smells

    targets: list[Path]
    if path.is_file():
        targets = [path]
    elif path.is_dir():
        targets = sorted(p for p in path.rglob("*.py") if ".venv" not in p.parts and "__pycache__" not in p.parts)
    else:
        console.print(f"[#f7768e]not a file or directory: {path}[/#f7768e]")
        raise typer.Exit(2)

    t = Thresholds(
        max_statements=max_statements,
        max_params=max_params,
        max_nesting=max_nesting,
        max_returns=max_returns,
        max_locals=max_locals,
    )
    total = 0
    high = 0
    for fp in targets:
        try:
            source = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        smells = detect_smells(source, t)
        if not smells:
            continue
        rel = fp if not fp.is_absolute() else fp.resolve()
        console.print(f"[bold]{rel}[/bold]")
        for s in smells:
            colour = "#f7768e" if s.severity == HIGH else "#e0af68"
            console.print(f"  [{colour}]{s.severity:<4}[/{colour}] L{s.line:<4} {s.kind:<16} {s.function}() — {s.message}")
            total += 1
            if s.severity == HIGH:
                high += 1
    if total == 0:
        console.print("[#9ece6a]✓ no smells found[/#9ece6a]")
        return
    console.print(f"\n[dim]{total} smell(s), {high} high-severity, across {len(targets)} file(s).[/dim]")
    if high > 0 or (strict and total > 0):
        raise typer.Exit(1)


# ---------- faithfulness (grounding harness) ----------

faithfulness_app = typer.Typer(
    help="Grounding harness: is an answer supported by the sources it cites? "
         "Decomposes claims, flags hallucinated code symbols, scores 0..1.",
)
util_app.add_typer(faithfulness_app, name="faithfulness")


@faithfulness_app.command("check")
def faithfulness_check(
    answer: list[str] = typer.Argument(None, help="The answer to check. Omit to read piped stdin."),
    sources: list[Path] = typer.Option(
        None, "--sources", "-s",
        help="Source files the answer should be grounded in (repeatable). "
             "These are the files the agent read.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the full report as JSON."),
    strict: bool = typer.Option(
        False, "--strict",
        help="Exit non-zero when the answer is ungrounded (1) or the harness "
             "abstains (2). Useful in CI.",
    ),
) -> None:
    """Score an ANSWER against the SOURCES it should be grounded in.

    Reads each --sources file, decomposes the answer into atomic claims, grounds
    each against the union of sources (content-word overlap plus exact code-symbol
    presence), flags hallucinated symbols (functions / paths / attributes absent
    from every source), and prints a calibrated 0..1 faithfulness score. The
    harness abstains when the evidence is too thin to judge honestly.

    Examples:
      ronin faithfulness check "login calls make_token" --sources auth.py
      cat answer.txt | ronin faithfulness check -s a.py -s b.py --json
    """
    from .faithfulness_cmd import check, exit_code, to_json
    from .faithfulness_hook import MODE_WARN, render

    text = " ".join(answer) if answer else ""
    if not sys.stdin.isatty():
        try:
            piped = sys.stdin.read().strip()
        except Exception:  # noqa: BLE001
            piped = ""
        if piped:
            text = f"{text}\n{piped}" if text else piped
    if not text.strip():
        console.print("[red]✗[/red] nothing to check - give an answer or pipe input.")
        raise typer.Exit(2)

    source_paths = list(sources or [])
    outcome, missing = check(text, source_paths, mode=MODE_WARN)

    if json_out:
        sys.stdout.write(to_json(outcome, missing=missing) + "\n")
    else:
        for m in missing:
            console.print(f"[yellow]![/yellow] could not read source: [dim]{m}[/dim]")
        render(outcome, console)

    if strict:
        code = exit_code(outcome)
        if code:
            raise typer.Exit(code)


# ---------- relay (remote access via a self-hosted relay) ----------

# The relay command group lives in its own module. Importing it pulls in only
# typer/rich; the heavy relay deps (fastapi, uvicorn, websockets) are imported
# lazily inside each subcommand body, so the core CLI import stays light and
# offline. `python -m ronin_relay` keeps working unchanged.
from .relay import relay_app

util_app.add_typer(relay_app, name="relay")


@app.command()
def play(
    game: str = typer.Argument(
        "", help="Game key to launch directly (e.g. 2048, snake, ttt). Omit for the menu."),
) -> None:
    """Take a break — play a free terminal game in ronin's arcade. 🎮"""
    try:
        from ronin_arcade.games import GAMES, find
    except ImportError:
        console.print("🐼 the arcade isn't installed — get 30+ free terminal games with: "
                      "[bold]pip install 'ronin-cli\\[arcade]'[/bold]")
        return
    from .picker import Choice, ask_choice
    from .theme import gradient_text

    # Jump straight into a game by key: `ronin play snake`.
    if game:
        g = find(game)
        if g is None:
            keys = ", ".join(sorted(x.key for x in GAMES))
            console.print(f"[yellow]No game called[/yellow] '{game}'.\n"
                          f"[dim]Available:[/dim] {keys}\n"
                          f"[dim]or run[/dim] [bold]ronin play[/bold] [dim]for the menu.[/dim]")
            raise typer.Exit(1)
        g.play(console)
        try:
            from ronin_arcade.gamify import record
            record("game_played")
        except Exception:  # noqa: BLE001
            pass
        return

    # Otherwise: pick from the menu, play, and return to the menu until you quit.
    console.print()
    console.print(gradient_text("  🐼  ronin arcade"))
    console.print(f"  [dim]{len(GAMES)} free games · pick one to play[/dim]")
    while True:
        choices = [Choice(label=f"{g.emoji}  {g.name}", value=g.key, description=g.desc)
                   for g in GAMES]
        choices.append(Choice(label="🚪  Quit", value="__quit__"))
        pick = ask_choice("Pick a game:", choices, console=console)
        if not pick or pick == "__quit__":
            console.print("  [dim]gg — come back soon! 🐼[/dim]")
            return
        g = find(pick)
        if g is not None:
            try:
                g.play(console)
            except (EOFError, KeyboardInterrupt):
                console.print("\n  [dim]back to the menu…[/dim]")
            else:
                try:
                    from ronin_arcade.gamify import record
                    record("game_played")
                except Exception:  # noqa: BLE001
                    pass
            console.print()


@util_app.command()
def route(
    task: list[str] = typer.Argument(..., help="The task to run on the auto-chosen blade."),
) -> None:
    """Cost-aware auto-router: classify the task, pick the **cheapest blade that
    clears the learned reliability bar** for that task class, run it, and report the
    savings vs the strong baseline. Frontier quality where it matters, $0 elsewhere —
    and it learns which blade is good enough per task type as you use it."""
    from .route import route as _route
    _route(load_config(), " ".join(task), console=console)


@util_app.command()
def swebench(
    models: str = typer.Option(
        ..., "--models", "-m",
        help="Comma-separated provider[:model] specs, e.g. 'anthropic,gemini,cerebras,ollama:llama3.1'."),
) -> None:
    """SWE-bench-style **objective** coding eval: each model fixes bundled bug 'issues';
    every fix is graded by *running the test* (exit code, no LLM judge), then ranked on a
    pass-rate / cost / time leaderboard. Local proxy — real SWE-bench needs Docker."""
    from .swebench import run as run_swebench
    run_swebench([s.strip() for s in models.split(",") if s.strip()], console=console)


@util_app.command()
def profile() -> None:
    """Your coding profile: XP, level, daily streak, and unlocked achievements —
    earned for real actions (tests passing, bugs fixed, commits, PRs)."""
    try:
        from ronin_arcade.gamify import render_profile
    except ImportError:
        console.print("🐼 profiles live in the arcade extra — "
                      "[bold]pip install 'ronin-cli\\[arcade]'[/bold]")
        return
    render_profile(console)


@util_app.command()
def xp(
    event: str = typer.Argument(..., help="The action to award XP for: test_passed, bug_fixed, commit, pr_opened, …"),
    n: int = typer.Option(1, "--n", help="How many of this event to record."),
) -> None:
    """Award XP for a coding action and show what changed (level-ups, streak, new badges).
    Hooks call this automatically; also handy to log a win by hand."""
    try:
        from ronin_arcade.gamify import record
    except ImportError:
        console.print("🐼 XP tracking lives in the arcade extra — "
                      "[bold]pip install 'ronin-cli\\[arcade]'[/bold]")
        return
    res = record(event, n=n)
    line = f"[#2dd4bf]✦ +{res['xp_gained']} XP[/#2dd4bf] · [bold]LV {res['level']}[/bold] {res['title']}"
    if res.get("streak"):
        line += f" · [#e0af68]🔥 {res['streak']}d[/#e0af68]"
    console.print(line)
    if res.get("leveled_up"):
        console.print(f"  [#9ece6a]⬆ level up![/#9ece6a]")
    for a in res.get("unlocked", []):
        console.print(f"  [#9ece6a]🏅 {a['name']}[/#9ece6a] — [dim]{a['desc']}[/dim]")


# ---------- index · scalable repo context for big codebases ----------
@dev_app.command()
def index(
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Preview the bounded context a task would front-load."),
    budget: int = typer.Option(8000, "--budget", help="Token budget for the --query preview."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root to index."),
) -> None:
    """Build/refresh a persistent, incremental repo index (.ronin/index.db) so the
    coding agent can pull the *right* files within a token budget on large repos —
    no more blowing the context window. `--query` previews what it would load."""
    from .repo_index import index as _repo_index
    _repo_index(root=root, query=query, budget=budget)


# ---------- stats · usage dashboard (sessions, tokens, streaks, heatmap) ----------
@util_app.command()
def stats() -> None:
    """📊 Your ronin at a glance — sessions, tokens, active-day streaks, peak hour,
    favorite model, and an activity heatmap. Read-only; counts only, never content."""
    from .stats import run as run_stats
    run_stats(console)


# ---------- do · universal action engine (grounded + approved, never pays) ----------
@util_app.command()
def do(
    task: list[str] = typer.Argument(
        ..., help="A real-world task: 'order me a pizza', 'book a flight to NYC', 'reserve a table for 2'."),
) -> None:
    """Take on a real-world task — research it (faithfulness-GROUNDED so it never
    invents an option or price), plan it, and carry it out step by step with YOUR
    approval on every move. ronin never pays: it drives the task up to the checkout
    line, then stops and hands you the final confirm. Do-anything, but safe."""
    from .act import run as _act
    _act(load_config(), " ".join(task), console=console)


# ---------- privacy / vault · data safety (encryption at rest + audit) ----------
@util_app.command()
def privacy() -> None:
    """🔐 Audit what ronin stores locally and what's protected — read-only, and it
    never prints secret VALUES, only whether each is encrypted/redacted. The
    "your data is safe" command."""
    from .vault import audit, default_home_paths, render_privacy
    render_privacy(console, audit(default_home_paths()))


vault_app = typer.Typer(help="🔒 Encrypt ronin's local data at rest (passphrase-derived, per-file key).")
util_app.add_typer(vault_app, name="vault")


def _vault_salt(path: str) -> bytes:
    import hashlib
    import os as _os
    return hashlib.sha256(_os.path.abspath(path).encode()).digest()[:16]


@vault_app.command("lock")
def vault_lock(
    path: str = typer.Argument(..., help="File to encrypt in place."),
    passphrase: str = typer.Option(..., prompt=True, hide_input=True),
) -> None:
    """Encrypt a file at rest."""
    from .vault import derive_key, lock_file
    lock_file(path, derive_key(passphrase, _vault_salt(path)))
    console.print(f"[green]🔒 locked {path}[/green]")


@vault_app.command("unlock")
def vault_unlock(
    path: str = typer.Argument(..., help="File to decrypt in place."),
    passphrase: str = typer.Option(..., prompt=True, hide_input=True),
) -> None:
    """Decrypt a vault-locked file (wrong passphrase is rejected; the file is left untouched)."""
    from .vault import InvalidToken, derive_key, unlock_file
    try:
        unlock_file(path, derive_key(passphrase, _vault_salt(path)))
        console.print(f"[green]🔓 unlocked {path}[/green]")
    except InvalidToken:
        console.print("[red]wrong passphrase or tampered file — left untouched[/red]")
        raise typer.Exit(1)


# ---------- gateway · safe multi-channel (allowlist + pairing, read-only) ----------
try:  # optional — never let a gateway import break the whole CLI
    from .gateway import gateway_app as _gateway_app
    util_app.add_typer(_gateway_app, name="gateway")
except Exception:  # noqa: BLE001
    pass


# ---------- skill registry · shareable prompt-template skills (no code execution) ----------
skill_app = typer.Typer(help="📦 Publish / install / search shareable skills — templates only, no code runs.")
util_app.add_typer(skill_app, name="skill")


@skill_app.command("publish")
def skill_publish(name: str = typer.Argument(..., help="The repo-local skill (/<name>) to publish.")) -> None:
    """Package a repo-local skill into the shared registry (safety-validated; templates only)."""
    from .skill_registry import publish
    res = publish(name, root=Path("."))
    console.print(f"[green]📦 published {res['name']}[/green] [dim]{res.get('summary', '')}[/dim]")


@skill_app.command("install")
def skill_install(name: str = typer.Argument(..., help="Skill name to install from the registry.")) -> None:
    """Install a skill (validated BEFORE it lands — refuses anything that smuggles executable payloads)."""
    from .skill_registry import install
    path = install(name, root=Path("."))
    console.print(f"[green]📦 installed → {path}[/green] [dim](run it as /{name})[/dim]")


@skill_app.command("search")
def skill_search(query: str = typer.Argument("", help="Search the registry (blank lists all).")) -> None:
    """Search shareable skills by name / summary / author."""
    from .skill_registry import search
    hits = search(query)
    if not hits:
        console.print("[dim]no skills found[/dim]")
        return
    for b in hits:
        m = getattr(b, "manifest", b)
        console.print(f"  [bold]{m['name']}[/bold] [dim]{m.get('summary', '')}[/dim]")


# ---------- bg · fire-and-forget background agents ----------
@util_app.command(name="bg")
def bg_cmd(
    task: list[str] = typer.Argument(None, help="Task to run in the background (quote multi-word)."),
    code: bool = typer.Option(False, "--code", help="Run the coding agent (read-only/plan; never auto-applies) instead of read-only ask."),
    list_: bool = typer.Option(False, "--list", help="Show a status table of background tasks."),
    result: str = typer.Option(None, "--result", help="Print the finished result for a task id."),
    cancel: str = typer.Option(None, "--cancel", help="Cancel a running task by id."),
) -> None:
    """🌙 Fire a long task into the background and get notified when it's done.
    Background runs default to READ-ONLY; --code plans/reads but never auto-edits."""
    from . import background as bg

    if list_:
        console.print(bg.summarize_tasks(bg.list_tasks()))
        return
    if result:
        rec = bg.get_task(result)
        if rec is None:
            console.print(f"[#f7768e]no background task {result!r}[/#f7768e]")
            raise typer.Exit(1)
        if rec["status"] not in ("done", "failed", "cancelled"):
            console.print(f"[dim]{result} is {rec['status']} — not finished yet (see `ronin bg --list`).[/dim]")
            return
        console.print(rec.get("result") or rec.get("error") or "(no output)")
        return
    if cancel:
        ok = bg.cancel_task(cancel)
        console.print(f"[green]✓ cancelled[/green] {cancel}" if ok else f"[#f7768e]no background task {cancel!r}[/#f7768e]")
        raise typer.Exit(0 if ok else 1)

    text = " ".join(task) if task else ""
    if not text.strip():
        console.print('[red]✗[/red] give me a task, e.g. [cyan]ronin bg "refactor the auth module"[/cyan]')
        raise typer.Exit(2)
    tid = bg.start_task(text, root=".", kind="code" if code else "ask")
    console.print(f"[green]✓ started[/green] background task [cyan]{tid}[/cyan] [dim]({'code/plan' if code else 'ask'})[/dim]")
    console.print(f"   [dim]status: [bold]ronin bg --list[/bold] · result: [bold]ronin bg --result {tid}[/bold][/dim]")


# ---------- checkpoint / undo · snapshot the repo, roll back a messy session ----------
@dev_app.command()
def checkpoint(
    label: list[str] = typer.Argument(None, help="Optional short label for the snapshot."),
    list_: bool = typer.Option(False, "--list", "-l", help="List saved checkpoints instead of creating one."),
) -> None:
    """📸 Snapshot the repo so you can `ronin undo` back to it. Non-destructive."""
    from .checkpoints import create_checkpoint, list_checkpoints, summarize_checkpoints
    root = Path.cwd()
    if list_:
        console.print(summarize_checkpoints([c.to_meta() for c in list_checkpoints(root)]))
        return
    cp = create_checkpoint(root, " ".join(label) if label else None)
    console.print(f"[green]✓[/green] checkpoint [bold]{cp.id}[/bold] [dim]({cp.files} file(s), {cp.kind})[/dim] — "
                  f"roll back with [bold]ronin undo {cp.id}[/bold]")


@dev_app.command()
def undo(
    checkpoint_id: str = typer.Argument(None, help="Checkpoint id to restore (default: the most recent)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """⏪ Roll the repo back to a checkpoint (most recent if no id). Reversible —
    the current state is snapshotted first, and untracked files are never deleted."""
    from .checkpoints import list_checkpoints, most_recent, restore_checkpoint
    root = Path.cwd()
    records = [c.to_meta() for c in list_checkpoints(root)]
    target = next((r for r in records if r["id"] == checkpoint_id), None) if checkpoint_id else most_recent(records)
    if target is None:
        console.print("[yellow]no checkpoints to undo[/yellow] — run [bold]ronin checkpoint[/bold] first.")
        raise typer.Exit(1)
    console.print(f"about to restore [bold]{target['id']}[/bold] [dim]({target.get('label', '')})[/dim]; "
                  "files created since are removed. [dim](current state is snapshotted first)[/dim]")
    if not yes:
        console.print("  [yellow]undo?[/yellow] [grey50]y / N[/grey50] ", end="")
        try:
            if input().strip().lower() not in ("y", "yes"):
                console.print("[dim]aborted[/dim]")
                raise typer.Exit(0)
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]aborted[/dim]")
            raise typer.Exit(0)
    res = restore_checkpoint(root, target["id"])
    console.print(f"[green]✓[/green] {res.message}" if res.ok else f"[red]✗[/red] {res.message}")


# ---------- loc · code-stats dashboard ----------
@dev_app.command()
def loc(
    root: Path = typer.Option(Path("."), "--root", help="Project root to scan."),
) -> None:
    """📐 Code-stats dashboard: lines of code by language, file counts, the biggest
    files, and comment ratio. Read-only; skips vendored/generated dirs."""
    from .loc import run as run_loc
    run_loc(root=str(root), console=console)


# ---------- todo-scan · TODO/FIXME/HACK comment board ----------
@dev_app.command("todo-scan")
def todo_scan(
    kind: str = typer.Option(None, "--kind", help="Only this marker: fixme|bug|todo|xxx|hack."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root to scan."),
) -> None:
    """🗒  Scan the codebase for TODO/FIXME/HACK/XXX/BUG comments — prioritized
    (FIXME/BUG first) and grouped by kind."""
    from .todo_scan import render_todos, scan_repo
    items = scan_repo(root)
    if kind:
        items = [it for it in items if it["kind"] == kind.upper()]
    render_todos(items, console)


# ---------- pr-draft · draft a PR title + body from the branch diff ----------
@dev_app.command("pr-draft")
def pr_draft_cmd(
    base: str = typer.Option("main", "--base", help="Base branch to compare against."),
    root: Path = typer.Option(Path("."), "--root", help="Repo root."),
) -> None:
    """🔀 Draft a clean PR title + body from this branch's changes vs --base, grounded
    in the actual diff (via ronin's provider layer). Heuristic fallback with no auth."""
    from .pr_draft import collect_branch_changes, draft, render_pr
    changes = collect_branch_changes(root, base=base)
    if changes["error"]:
        console.print(f"[red]{changes['error']}.[/red]")
        raise typer.Exit(1)
    if changes["branch"] in ("main", "master"):
        console.print(f"[yellow]you're on '{changes['branch']}' — make a feature branch first.[/yellow]")
        raise typer.Exit(1)
    if not changes["commits"]:
        console.print(f"[yellow]no commits on '{changes['branch']}' vs '{base}'.[/yellow]")
        raise typer.Exit(1)
    console.print(f"[#7aa2f7]🔀 {changes['branch']} → {base} ({len(changes['commits'])} commit(s))[/#7aa2f7]")
    config = load_config()
    with console.status("[dim] drafting pull request…[/dim]", spinner="dots"):
        title, body = draft(config, root, base=base)
    render_pr(title, body, console)


# ---------- local · run ronin on a fully local open model (zero API key) ----------
@util_app.command()
def local(
    model: str = typer.Option(None, "--model", help="Ollama model tag (default: sized to your RAM, e.g. qwen2.5-coder:7b)."),
    embedded: bool = typer.Option(False, "--embedded", help="Run on the IN-PROCESS embedded model — no Ollama, no key (pip install 'ronin-cli[local]')."),
) -> None:
    """🏠 Run ronin on a FULLY LOCAL open-source model — ZERO API key. Default uses
    Ollama; --embedded runs the model IN-PROCESS (no Ollama daemon at all). Nothing
    leaves your machine. The answer to 'why an API for everything'."""
    if embedded:
        from .local_setup import run_embedded
        run_embedded(console)
        return
    from .local_setup import run as run_local
    run_local(console, model=model)


# ---------- finetune · train ronin's own brain from your sessions ----------
finetune_app = typer.Typer(help="🧠 Fine-tune an open coding model on YOUR ronin sessions, then serve it locally via Ollama. (Training runs on a rented GPU.)")
util_app.add_typer(finetune_app, name="finetune")


@finetune_app.command("collect")
def finetune_collect(
    root: Path = typer.Option(Path("."), "--root", help="Repo whose .ronin/sessions to mine."),
) -> None:
    """Mine your archived sessions into a PII-scrubbed training dataset (~/.ronin/finetune/dataset.jsonl)."""
    from .finetune import run_collect
    run_collect(console, root=root)


@finetune_app.command("script")
def finetune_script(
    base_model: str = typer.Option("unsloth/Qwen2.5-Coder-7B-bnb-4bit", "--base-model", help="Open coder to fine-tune."),
) -> None:
    """Emit a runnable QLoRA train.py + a rent-a-GPU README (Colab/Modal/RunPod). ronin can't train locally — needs a GPU."""
    from .finetune import run_script
    run_script(console, base_model=base_model)


@finetune_app.command("serve")
def finetune_serve(
    gguf: Path = typer.Argument(..., help="Path to the fine-tuned .gguf from training."),
) -> None:
    """Write the Ollama Modelfile for a fine-tuned GGUF + the `ollama create` / `ronin local` steps to serve it."""
    from .finetune import run_serve
    run_serve(console, gguf)


if __name__ == "__main__":  # pragma: no cover
    app()
