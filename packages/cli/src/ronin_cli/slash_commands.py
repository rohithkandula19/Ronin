"""In-session slash-command handlers, as a dispatch table.

`code_mode.handle_slash_command` is a thin parser that looks the command up in
``SLASH_DISPATCH`` and calls the matching handler with a ``SlashCtx``. Splitting
the handlers out of one giant if/elif chain dropped that function's cyclomatic
complexity from ~105 to single digits and made each command independently
readable. Handlers that need helpers defined in ``code_mode`` import it lazily to
avoid a circular import (and so test monkeypatches on ``code_mode`` are honored).

Every handler returns ``"handled"`` (command ran → loop again) or ``"exit"``
(quit the session).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rich.console import Console

from .config import RoninConfig


@dataclass
class SlashCtx:
    """Everything a slash handler might touch, in one bag."""
    parts: list[str]
    console: Console
    root: Path | str
    config: RoninConfig
    undo_stack: list
    transcript: list[str]
    # Structured cross-turn Message history the session feeds back to the agent.
    # Handlers that reset the conversation (clear/compact/resume) must clear this
    # too, in place, so it stays consistent with ``transcript``. Defaults to an
    # empty list so callers that don't track it (tests, one-shots) still work.
    message_history: list = field(default_factory=list)

    @property
    def cmd(self) -> str:
        return self.parts[0].lower()


def _slash_quit(ctx: SlashCtx) -> str:
    ctx.console.print("[dim]bye[/dim]")
    return "exit"


def _slash_help(ctx: SlashCtx) -> str:
    from . import code_mode
    code_mode._render_help(ctx.console)
    return "handled"


def _slash_login(ctx: SlashCtx) -> str:
    # Switch provider + set its key from inside the session — masked input, saved
    # to local config, applied immediately. Usage: /login [provider] [model]
    from rich.prompt import Prompt

    from .config import PROVIDER_PRESETS, save_config
    parts, console, config = ctx.parts, ctx.console, ctx.config
    old_provider = config.provider
    prov = parts[1].lower() if len(parts) > 1 else config.provider
    config.provider = prov
    if len(parts) > 2:
        config.model = parts[2]
    elif prov != old_provider:
        # Switching providers: drop the old provider's model so the new provider's
        # default applies (a Gemini login shouldn't keep a Qwen model).
        config.model = None
    known = ", ".join(PROVIDER_PRESETS)
    if prov not in PROVIDER_PRESETS and prov != "custom":
        console.print(f"  [yellow]unknown provider[/yellow] '{prov}' — known: {known}, custom")
        return "handled"
    if prov == "ollama":
        config.demo_mode = False
        path = save_config(config)
        console.print(f"  [green]✓[/green] provider → [bold]ollama[/bold] [dim](local, no key)[/dim] · {path}")
        return "handled"
    key = (Prompt.ask(f"  API key for [bold]{prov}[/bold] (input hidden — paste ONCE, then Enter)",
                      password=True, console=console) or "").strip()
    if not key:
        console.print("  [yellow]no key entered — nothing changed[/yellow]")
        return "handled"
    if key.count("sk-") >= 2 or len(key) > 400:
        console.print("  [red]✗ that looks pasted more than once — run [bold]/login[/bold] again and paste it once[/red]")
        return "handled"
    config.demo_mode = False
    config.set_key_for(prov, key)  # per-provider — never clobbers another provider's key
    path = save_config(config)
    console.print(f"  [green]✓[/green] [bold]{prov}[/bold] [dim]({config.resolved_model()})[/dim] "
                  f"— saved to [cyan]{path}[/cyan]; using it from your next message.")
    return "handled"


def _slash_clear(ctx: SlashCtx) -> str:
    ctx.transcript.clear()
    ctx.message_history.clear()  # keep structured history in sync with the text one
    ctx.console.print("[dim]✓ conversation cleared[/dim]")
    return "handled"


def _slash_undo(ctx: SlashCtx) -> str:
    from .code_tools import undo_last
    ctx.console.print(f"[yellow]↩[/yellow] {undo_last(ctx.undo_stack)}")
    return "handled"


def _slash_diff(ctx: SlashCtx) -> str:
    from . import code_mode
    code_mode._show_git_diff(ctx.console, ctx.root)
    return "handled"


def _slash_commit(ctx: SlashCtx) -> str:
    from .git_helper import commit as _commit
    _commit(ctx.console, ctx.root, ctx.config)
    return "handled"


def _slash_pr(ctx: SlashCtx) -> str:
    from .git_helper import open_pr
    open_pr(ctx.console, ctx.root, ctx.config)
    return "handled"


def _slash_model(ctx: SlashCtx) -> str:
    parts, console, config = ctx.parts, ctx.console, ctx.config
    if len(parts) > 1:  # /model <name> → switch model in place, no key re-entry
        from .config import save_config
        config.model = " ".join(parts[1:])
        save_config(config)
        console.print(f"  [green]✓[/green] model → [bold]{config.resolved_model()}[/bold] "
                      f"[dim](provider {config.provider})[/dim]; using it from your next message.")
        return "handled"
    console.print(
        f"[dim]provider[/dim] [bold]{config.provider}[/bold]  ·  "
        f"[dim]model[/dim] [bold]{config.resolved_model()}[/bold]"
    )
    console.print("[dim]switch with [bold]/model <name>[/bold] · list with [bold]/models[/bold][/dim]")
    return "handled"


def _slash_verify(ctx: SlashCtx) -> str:
    from .config import save_config
    parts, console, config = ctx.parts, ctx.console, ctx.config
    if len(parts) >= 2 and parts[1].lower() in ("on", "true", "yes"):
        config.verify = True
    elif len(parts) >= 2 and parts[1].lower() in ("off", "false", "no"):
        config.verify = False
    else:
        console.print(f"  [dim]self-verify is [bold]{'on' if config.verify else 'off'}[/bold] · "
                      "toggle with [bold]/verify on|off[/bold][/dim]")
        return "handled"
    save_config(config)
    console.print(f"  [green]✓[/green] self-verify [bold]{'on' if config.verify else 'off'}[/bold]"
                  + (" — the agent will review & fix its own work after edits." if config.verify else ""))
    return "handled"


def _slash_route(ctx: SlashCtx) -> str:
    from .config import save_config
    parts, console, config = ctx.parts, ctx.console, ctx.config
    if len(parts) >= 2 and parts[1].lower() in ("off", "none"):
        config.route_fast = config.route_strong = None
        save_config(config)
        console.print("  [green]✓[/green] routing off — using a single model.")
        return "handled"
    if len(parts) >= 3:
        config.route_fast, config.route_strong = parts[1], parts[2]
        save_config(config)
        console.print(f"  [green]✓[/green] routing on · simple→[bold]{parts[1]}[/bold] · "
                      f"complex→[bold]{parts[2]}[/bold]")
        return "handled"
    if config.route_fast and config.route_strong:
        console.print(f"  [dim]routing on · simple→[bold]{config.route_fast}[/bold] · "
                      f"complex→[bold]{config.route_strong}[/bold][/dim]")
    else:
        console.print("  [dim]routing off. Enable: [bold]/route <fast-model> <strong-model>[/bold] "
                      "(e.g. /route llama3.1-8b gpt-oss-120b)[/dim]")
    return "handled"


def _slash_models(ctx: SlashCtx) -> str:
    from rich.columns import Columns
    from rich.text import Text

    from . import code_mode
    console, config = ctx.console, ctx.config
    names = code_mode._list_provider_models(config)
    if not names:
        console.print(f"[dim]couldn't list models for [bold]{config.provider}[/bold] "
                      f"(set a key with [bold]/login[/bold] first).[/dim]")
        return "handled"
    current = config.resolved_model()
    items = [
        Text(f"● {n}", style="bold #9ece6a") if n == current else Text(f"  {n}", style="cyan")
        for n in names[:60]
    ]
    console.print(f"[bold]{config.provider}[/bold] models [dim]({len(names)})[/dim] "
                  "[#6b7089]· ● = active[/#6b7089]")
    console.print(Columns(items, padding=(0, 3), equal=True))
    console.print("[dim]switch with [bold]/model <name>[/bold][/dim]")
    return "handled"


def _slash_memory(ctx: SlashCtx) -> str:
    from rich.panel import Panel

    from .memory_store import load_memories
    from .project_memory import load_project_memory
    console, root = ctx.console, ctx.root
    mems = load_memories()
    if mems:
        body = "\n".join(f"[#2dd4bf]•[/#2dd4bf] {m['text']}" for m in mems[-40:])
        console.print(Panel(body, title=f"🧠 remembers about you ({len(mems)})", border_style="#2dd4bf"))
    else:
        console.print("[dim]no long-term memories yet — I'll save durable facts as we talk.[/dim]")
    found = load_project_memory(root)
    if found is not None:
        name, text = found
        console.print(Panel(text, title=name, border_style="cyan"))
    return "handled"


def _slash_init(ctx: SlashCtx) -> str:
    from .project_memory import write_memory_template
    path = write_memory_template(ctx.root)
    ctx.console.print(f"[green]✓[/green] project memory at [cyan]{path}[/cyan]")
    return "handled"


def _slash_tools(ctx: SlashCtx) -> str:
    from . import code_mode
    code_mode._render_tools(ctx.console, ctx.root)
    return "handled"


def _slash_cost(ctx: SlashCtx) -> str:
    from .savings import load_summary
    console, root = ctx.console, ctx.root
    s = load_summary(root)
    if not s["turns"]:
        console.print("[dim]no routed turns yet — set up the Cost Router with "
                      "[bold]ronin setup[/bold] to track savings.[/dim]")
        return "handled"
    console.print(f"[bold]💰 Cost Router[/bold] [dim](lifetime)[/dim]")
    console.print(f"  routed turns   [bold]{s['turns']}[/bold] "
                  f"[dim]({s['free_turns']} free)[/dim]")
    console.print(f"  spent          [bold]${s['spent']:.4f}[/bold]")
    console.print(f"  saved          [green]${s['saved']:.4f}[/green] [dim]vs all-strong[/dim]")
    return "handled"


def _slash_mcp(ctx: SlashCtx) -> str:
    from rich.table import Table as _Table

    from .mcp_client import _ACTIVE, load_mcp_servers
    console, root = ctx.console, ctx.root
    configured = load_mcp_servers(root)
    live = {c.name: c for c in _ACTIVE}
    if not configured and not live:
        console.print("[dim]no MCP servers configured — add one with "
                      "[bold]ronin mcp add <name> <command>[/bold][/dim]")
        return "handled"
    grid = _Table.grid(padding=(0, 2))
    grid.add_column(style="cyan", no_wrap=True)
    grid.add_column()
    seen = set()
    for name, spec in configured.items():
        seen.add(name)
        c = live.get(name)
        if c is not None:
            status = f"[green]● connected[/green] [dim]· {len(c.tools)} tool(s)[/dim]"
        else:
            status = "[dim]configured (not started this session)[/dim]"
        grid.add_row(name, f"{status}  [dim]{spec.get('command', '')}[/dim]")
    for name, c in live.items():  # live but not in the config file (rare)
        if name not in seen:
            grid.add_row(name, f"[green]● connected[/green] [dim]· {len(c.tools)} tool(s)[/dim]")
    console.print("[bold]MCP servers[/bold]")
    console.print(grid)
    return "handled"


def _slash_integrations(ctx: SlashCtx) -> str:
    from .mcp_client import load_mcp_servers
    from .mcp_catalog import CATALOG
    from .plugin_library import LIBRARY, installed_names
    console, root = ctx.console, ctx.root
    servers = load_mcp_servers(root)
    plugins = sorted(installed_names(root))
    console.print("[bold]🔌 integrations[/bold]")
    if servers:
        console.print(f"  [cyan]MCP servers[/cyan] [dim]({len(servers)})[/dim]  "
                      + ", ".join(servers))
    else:
        console.print("  [dim]no MCP servers — add one: [bold]ronin mcp install github[/bold] "
                      "(browse: [bold]ronin mcp catalog[/bold])[/dim]")
    if plugins:
        shown = ", ".join(plugins[:18]) + (f" +{len(plugins) - 18} more" if len(plugins) > 18 else "")
        console.print(f"  [cyan]plugins[/cyan] [dim]({len(plugins)})[/dim]  {shown}")
    else:
        console.print("  [dim]no plugins — add one: [bold]ronin plugin add weather[/bold] "
                      "(browse: [bold]ronin plugin library[/bold])[/dim]")
    console.print(f"[dim]{len(LIBRARY)} built-in plugins · {len(CATALOG)}-server catalog · or "
                  "[bold]ronin plugin new[/bold] to build your own.[/dim]")
    return "handled"


def _slash_agents(ctx: SlashCtx) -> str:
    from rich.table import Table as _Table
    console = ctx.console
    grid = _Table.grid(padding=(0, 2))
    grid.add_column(style="cyan", no_wrap=True)
    grid.add_column()
    grid.add_row("task", "read-only sub-agent — explores the codebase and reports "
                         "back; the agent spawns it to scope big multi-part jobs")
    grid.add_row("parallel_task", "fan out SEVERAL read-only sub-agents at once and "
                                  "collect all their findings together")
    grid.add_row("isolated_task", "MUTATING sub-agents, each in its own git worktree "
                                  "in parallel — their edits can't collide")
    grid.add_row("orchestrate", "decomposes a big goal into a plan of subagents "
                                "(research + isolated edits), runs them, synthesizes one "
                                "result; each can run on a different provider")
    console.print("[bold]sub-agents[/bold]")
    console.print(grid)
    console.print("[dim]the agent delegates to these on its own; you don't call them directly. "
                  "For a standalone run: [/dim][cyan]ronin orchestrate \"<goal>\" --roster anthropic,gemini[/cyan]")
    return "handled"


def _slash_copy(ctx: SlashCtx) -> str:
    from . import code_mode
    console, transcript = ctx.console, ctx.transcript
    reply = code_mode._last_assistant_reply(transcript)
    if not reply:
        console.print("[dim]nothing to copy yet[/dim]")
    elif code_mode._copy_to_clipboard(reply):
        console.print(f"[green]✓[/green] copied last reply [dim]({len(reply)} chars)[/dim]")
    else:
        console.print("[yellow]no clipboard tool found[/yellow] [dim](install pbcopy/xclip/xsel)[/dim]")
    return "handled"


def _slash_resume(ctx: SlashCtx) -> str:
    # /resume → list past sessions (a picker); /resume <n> → reload session n.
    from .sessions import list_sessions, load_session as _load_sess, set_current_session
    parts, console, root, transcript = ctx.parts, ctx.console, ctx.root, ctx.transcript
    sess = list_sessions(root)
    if not sess:
        console.print("[dim]no past sessions for this project yet[/dim]")
        return "handled"
    if len(parts) > 1 and parts[1].isdigit():
        idx = int(parts[1]) - 1
        if not (0 <= idx < len(sess)):
            console.print(f"[yellow]no session #{parts[1]}[/yellow] — run [bold]/resume[/bold] to list")
            return "handled"
        chosen = sess[idx]
        transcript.clear()
        transcript.extend(_load_sess(chosen["id"]))
        # Drop live structured history — the loaded session is text-only, so the
        # next turn re-seeds from the text tail (history_prefix) and rebuilds.
        ctx.message_history.clear()
        set_current_session(chosen["id"])   # continue this one
        console.print(f"[green]✓[/green] resumed [dim]{chosen['turns']} turns · "
                      f"{chosen['title']!r}[/dim]", highlight=False)
        return "handled"
    console.print(f"[bold]past sessions[/bold] [dim]· {len(sess)} · this project[/dim]")
    for i, s in enumerate(sess[:20], 1):
        when = (s["updated"][:16].replace("T", " ")) or "?"
        console.print(f"  [cyan]{i:>2}[/cyan]  [#6b7089]{when}[/#6b7089]  "
                      f"{s['turns']:>2} turns  {s['title'][:58]}", highlight=False)
    console.print("[dim]reload one with [bold]/resume <number>[/bold][/dim]", highlight=False)
    return "handled"


def _slash_export(ctx: SlashCtx) -> str:
    import datetime as _dt
    from pathlib import Path as _Path
    console, root, transcript = ctx.console, ctx.root, ctx.transcript
    if not transcript:
        console.print("[dim]nothing to export yet[/dim]")
        return "handled"
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = _Path(root).resolve() / f"ronin-conversation-{stamp}.md"
    lines = ["# ronin conversation", ""]
    for e in transcript:
        if e.startswith("USER: "):
            lines += [f"## You", "", e[len("USER: "):], ""]
        elif e.startswith("ASSISTANT: "):
            lines += [f"## ronin", "", e[len("ASSISTANT: "):], ""]
        else:
            lines += [e, ""]
    try:
        out.write_text("\n".join(lines), encoding="utf-8")
        console.print(f"[green]✓[/green] exported → [cyan]{out}[/cyan]")
    except OSError as e:
        console.print(f"[red]export failed:[/red] {e}")
    return "handled"


def _slash_compact(ctx: SlashCtx) -> str:
    from . import code_mode
    code_mode._compact_transcript(ctx.config, ctx.transcript, ctx.console)
    # The text transcript is now a single summary entry; drop the structured
    # history so the next turn re-seeds from that summary instead of double-
    # carrying the full (now-summarized) message list.
    ctx.message_history.clear()
    return "handled"


def _slash_fix(ctx: SlashCtx) -> str:
    """Red-to-green: run this repo's tests and, if they fail, hand the failure to
    ronin to repair — re-running until green (or a few attempts)."""
    console, config, root = ctx.console, ctx.config, ctx.root
    from .repair import repair_to_green
    from .verify_cmd import detect_verify_command

    detected = detect_verify_command(root)
    if detected is None:
        console.print("[yellow]no test command detected[/yellow] — add a "
                      "[bold]verify: <command>[/bold] line to RONIN.md.")
        return "handled"
    cmd, runner = detected
    console.print(f"[#7aa2f7]✅ verify[/#7aa2f7] [dim]({runner})[/dim] [cyan]{' '.join(cmd)}[/cyan]")
    if not config.has_provider_auth():
        # no key → just report, don't try to repair
        from .verify_cmd import run_verify
        v = run_verify(root)
        console.print("[green]✓ green[/green]" if v.passed else f"[#f7768e]✗ red[/#f7768e] [dim]{v.summary}[/dim]")
        return "handled"

    from .code_mode import run_code_agent

    def _agent(prompt: str) -> None:
        run_code_agent(config, prompt, root=root, console=console, max_iterations=25)

    def _on_round(attempt: int, v) -> None:
        console.print(f"[#6b7089]↻ repair {attempt} — handing the failure to ronin…[/#6b7089] "
                      f"[dim]{v.summary}[/dim]")

    final = repair_to_green(root, _agent, on_round=_on_round)
    if not final.ran:
        console.print(f"[dim]{final.summary}[/dim]")
    elif final.passed:
        console.print(f"[green]✓ green[/green] [dim]{final.summary}[/dim]")
    else:
        console.print(f"[#f7768e]✗ still red[/#f7768e] [dim]{final.summary}[/dim]")
    return "handled"


def _slash_context(ctx: SlashCtx) -> str:
    from . import code_mode
    console, config, transcript = ctx.console, ctx.config, ctx.transcript
    turns = sum(1 for e in transcript if e.startswith("USER: "))
    # Estimate from the STRUCTURED history the model actually receives — it
    # carries tool calls + tool results (file dumps, command output) that the
    # text transcript never records, so transcript alone drastically under-counts.
    # Fall back to the transcript text when there's no structured history yet.
    if ctx.message_history:
        convo = _serialize_history(ctx.message_history)
        entries = f"{len(ctx.message_history)} messages"
    else:
        convo = "\n".join(transcript)
        entries = f"{len(transcript)} entries"
    toks = code_mode._estimate_tokens(convo)
    window = 120_000 if config.provider == "anthropic" else 28_000
    pct = min(100, round(toks / window * 100)) if window else 0
    console.print("[bold]context[/bold]")
    console.print(f"  {code_mode.render_bar(toks, window)} [dim]~{toks:,} / {window:,} tokens "
                  f"({pct}%)[/dim]", highlight=False)
    console.print(f"  [#6b7089]{turns} turns · {entries} · "
                  f"~{len(convo):,} chars[/#6b7089]", highlight=False)
    console.print("  [dim]shrink with [bold]/compact[/bold] · wipe with [bold]/clear[/bold][/dim]")
    return "handled"


def _serialize_history(messages: list) -> str:
    """Flatten a structured Message history to text for size estimation —
    includes tool-call arguments and tool results, the bulk of real context."""
    import json
    parts: list[str] = []
    for m in messages:
        parts.append(getattr(m, "content", "") or "")
        for tc in (getattr(m, "tool_calls", None) or []):
            try:
                parts.append(json.dumps(tc.arguments, default=str))
            except (TypeError, ValueError):
                pass
    return "\n".join(parts)


def _slash_status(ctx: SlashCtx) -> str:
    from .dashboard import gather, render
    render(ctx.console, gather(ctx.root, ctx.config))
    return "handled"


def _slash_router(ctx: SlashCtx) -> str:
    from .router_stats import load_stats, rows
    console, config, root = ctx.console, ctx.config, ctx.root
    if not (config.route_fast and config.route_strong):
        console.print("[dim]routing off — set it up with [bold]/route <fast> <strong>[/bold] "
                      "or [bold]ronin setup[/bold].[/dim]")
        return "handled"
    console.print(f"[bold]router[/bold] [dim]· simple→[/dim][cyan]{config.route_fast}[/cyan] "
                  f"[dim]· complex→[/dim][cyan]{config.route_strong}[/cyan]")
    data = rows(load_stats(root))
    if not data:
        console.print("  [dim]no learned stats yet — run a few turns.[/dim]")
        return "handled"
    _st = {"reliable": "[green]reliable[/green]", "escalate": "[red]escalate[/red]",
           "learning": "[#6b7089]learning[/#6b7089]"}
    for r in data:
        rate = f"{r['rate']*100:.0f}%" if r["rate"] is not None else "—"
        console.print(f"  [dim]{r['tier']:<8}[/dim] [bold]{r['provider']:<12}[/bold] "
                      f"{r['ok']}/{r['total']} [cyan]{rate:>4}[/cyan]  {_st.get(r['status'], '')}")
    return "handled"


def _slash_vim(ctx: SlashCtx) -> str:
    from .prompt_box import set_vi_mode
    parts, console = ctx.parts, ctx.console
    want = None
    if len(parts) >= 2 and parts[1].lower() in ("on", "true", "yes"):
        want = True
    elif len(parts) >= 2 and parts[1].lower() in ("off", "false", "no"):
        want = False
    now_on = set_vi_mode(want)
    console.print(f"[green]✓[/green] vi keybindings [bold]{'on' if now_on else 'off'}[/bold]"
                  + (" [dim](Esc for normal mode)[/dim]" if now_on else ""))
    return "handled"


def _slash_doctor_config(ctx: SlashCtx) -> str:
    from rich.table import Table as _Table

    from . import __version__
    from .config import find_config_path
    console, config, cmd = ctx.console, ctx.config, ctx.cmd
    path = find_config_path()
    grid = _Table.grid(padding=(0, 2))
    grid.add_column(style="cyan", no_wrap=True)
    grid.add_column()
    grid.add_row("config file", str(path) if path else "[red]none — run ronin init[/red]")
    grid.add_row("provider", config.provider)
    grid.add_row("model", config.resolved_model())
    if config.resolved_base_url():
        grid.add_row("base_url", config.resolved_base_url() or "")
    if cmd == "doctor":
        grid.add_row(f"{config.provider} key",
                     "[green]present[/green]" if config.has_provider_auth() else "[red]missing[/red]")
    services = config.configured_services()
    grid.add_row("services", ", ".join(services) if services else "[dim]none[/dim]")
    grid.add_row("ronin", __version__)
    console.print(grid)
    if cmd == "doctor":
        console.print("[dim]live key+model check: [bold]ronin doctor --check[/bold][/dim]")
    return "handled"


def _slash_permissions(ctx: SlashCtx) -> str:
    """Show the standing allow/deny rules the approval gate remembers, or wipe
    them with ``/permissions clear``."""
    from .permissions import clear_rules, load_rules
    console, root, parts = ctx.console, ctx.root, ctx.parts
    if len(parts) > 1 and parts[1].lower() in ("clear", "reset"):
        clear_rules(root)
        console.print("[dim]✓ cleared your permission rules for this project "
                      "(committed deny-rules in settings.json stay)[/dim]")
        return "handled"
    rules = load_rules(root).rules
    if not rules:
        console.print("[dim]no standing permission rules — answer [bold]a[/bold] (always) "
                      "at an approval prompt to add one[/dim]")
        return "handled"
    console.print(f"[bold]permission rules[/bold] [dim]· {len(rules)} · .ronin/settings.json[/dim]")
    for r in rules:
        colour = "#9ece6a" if r.action == "allow" else "#f7768e"
        console.print(f"  [{colour}]{r.action:<5}[/{colour}] [cyan]{r.tool}[/cyan] "
                      f"[dim]{r.match}[/dim]", highlight=False)
    console.print("  [dim]wipe with [bold]/permissions clear[/bold] · "
                  "edit globs in [bold].ronin/settings.json[/bold][/dim]")
    return "handled"


# command name (and aliases) → handler
SLASH_DISPATCH: dict[str, Callable[[SlashCtx], str]] = {
    "q": _slash_quit, "quit": _slash_quit, "exit": _slash_quit,
    "help": _slash_help, "h": _slash_help, "?": _slash_help,
    "login": _slash_login, "key": _slash_login,
    "clear": _slash_clear,
    "undo": _slash_undo,
    "diff": _slash_diff,
    "commit": _slash_commit,
    "pr": _slash_pr,
    "model": _slash_model,
    "verify": _slash_verify,
    "route": _slash_route,
    "models": _slash_models,
    "memory": _slash_memory,
    "init": _slash_init,
    "tools": _slash_tools,
    "cost": _slash_cost, "costs": _slash_cost,
    "mcp": _slash_mcp,
    "integrations": _slash_integrations, "int": _slash_integrations,
    "agents": _slash_agents,
    "copy": _slash_copy,
    "resume": _slash_resume,
    "export": _slash_export,
    "compact": _slash_compact,
    "context": _slash_context,
    "status": _slash_status,
    "router": _slash_router,
    "vim": _slash_vim,
    "permissions": _slash_permissions, "perms": _slash_permissions,
    "fix": _slash_fix,
    "doctor": _slash_doctor_config, "config": _slash_doctor_config,
}
