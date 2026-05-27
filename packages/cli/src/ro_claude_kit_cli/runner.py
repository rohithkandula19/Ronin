"""Agent execution glue — wraps ReActAgent + the configured tool set + the configured LLM provider."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner

from ro_claude_kit_agent_patterns import (
    AnthropicProvider,
    LLMProvider,
    OpenAICompatProvider,
    ReActAgent,
    Tool,
)
from ro_claude_kit_hardening import InjectionScanner

from .config import CSKConfig
from .demo_brain import demo_answer
from .tools import build_tools


SYSTEM_PROMPT_TEMPLATE = """You are ronin — a CLI agent that helps a startup founder query their own data.
You have read-only tools for the configured services. You do NOT have write access; refuse politely
if asked to mutate state.

When you call a tool, prefer specific filters (customer_id, state, etc.) over listing everything.
Synthesize a tight answer; cite the data you used. If the user's question can't be answered with the
tools you have, say so honestly.

Configured services: {services}.
"""


def _friendly_provider_error(e: Exception, config: CSKConfig) -> str:
    """Turn a provider/network exception into a clean, actionable message
    (no traceback). Recognises auth failures and connection problems."""
    provider = config.provider
    key_env = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    status = getattr(getattr(e, "response", None), "status_code", None)
    name = e.__class__.__name__

    if status in (401, 403):
        return (
            f"[red]✗ {provider} rejected the request ({status} Unauthorized).[/red] "
            f"Your API key is missing or invalid.\n"
            f"[dim]Fix: set a valid key — [bold]export {key_env}=...[/bold] — or run "
            f"[bold]ronin init[/bold] to reconfigure. Check status with [bold]ronin doctor[/bold]. "
            f"To switch back to Claude: [bold]ronin init[/bold] and pick anthropic.[/dim]"
        )
    if status == 429:
        # Surface the provider's actual reason (e.g. "free-models-per-day").
        detail = ""
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                body = resp.json()
                if isinstance(body, dict):
                    detail = str((body.get("error") or {}).get("message") or "")
            except Exception:  # noqa: BLE001 - streamed/empty bodies
                detail = ""
        detail = f" [dim]({detail[:160]})[/dim]" if detail else ""
        return (
            f"[red]✗ {provider} rate-limited the request (429).[/red]{detail}\n"
            f"[dim]Free tiers cap usage per-minute and per-day. Wait and retry, or switch to a "
            f"provider with a separate free quota — in-session: [bold]/login gemini[/bold] or "
            f"[bold]/login groq[/bold].[/dim]"
        )
    if status is not None:
        return f"[red]✗ {provider} returned HTTP {status}.[/red] [dim]Check your config / model name.[/dim]"
    if name in ("ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutException"):
        return (f"[red]✗ couldn't reach {provider}.[/red] "
                f"[dim]Check your network or base_url. ({name})[/dim]")
    return f"[red]✗ provider error ({name}):[/red] [dim]{str(e)[:200]}[/dim]"


@dataclass
class AgentResultRich:
    success: bool
    output: str
    iterations: int
    trace: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    demo_mode: bool = False


def _serialize_trace(trace: list) -> list[dict[str, Any]]:
    return [{"kind": s.kind, "content": s.content} for s in trace]


def build_provider(config: CSKConfig) -> LLMProvider:
    """Construct the LLMProvider for this config.

    - ``anthropic`` → ``AnthropicProvider``
    - everything else (``ollama``, ``openai``, ``together``, ``groq``, ``fireworks``,
      ``custom``) → ``OpenAICompatProvider`` with the right ``base_url``.
    """
    model = config.resolved_model()
    base_url = config.resolved_base_url()

    if config.provider == "anthropic":
        return AnthropicProvider(
            model=model,
            api_key=config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"),
        )

    api_key = config.openai_api_key or os.environ.get("OPENAI_API_KEY")
    if config.provider == "ollama" and not api_key:
        api_key = "ollama"  # local; placeholder header
    return OpenAICompatProvider(
        model=model,
        base_url=base_url or "https://api.openai.com/v1",
        api_key=api_key,
    )


def _has_real_provider_key(config: CSKConfig) -> bool:
    if config.provider == "anthropic":
        return bool(config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"))
    if config.provider == "ollama":
        return True
    return bool(config.openai_api_key or os.environ.get("OPENAI_API_KEY"))


def _build_agent(config: CSKConfig, tools: list[Tool], *, extra_system: str = "") -> ReActAgent:
    services = config.configured_services()
    system = SYSTEM_PROMPT_TEMPLATE.format(services=", ".join(services) or "none")
    if extra_system:
        system += "\n\n" + extra_system
    provider = build_provider(config)
    return ReActAgent(
        system=system,
        tools=tools,
        provider=provider,
        max_iterations=8,
    )


# Capabilities note for the conversational front door (`ronin` / `ronin chat`).
# The base prompt is data-and-read-only; this grants the media tools so the
# agent doesn't refuse to "create" things.
CHAT_CAPABILITIES = """Beyond answering data questions, you can DO things for the user via tools:
- generate_image — draw/create an actual picture, logo, or art.
- generate_video — make a short video/animation.
- speak — read something aloud (text-to-speech).
When the user clearly wants media in plain language (e.g. "generate me a naruto image",
"make a video of a city at night", "say hello"), CALL the matching tool — don't say you can't.

IMPORTANT — don't confuse media with code:
- Only call generate_image when the user wants an actual IMAGE produced.
- If the user asks you to WRITE or SHOW CODE — even code that creates an image
  (e.g. "write code to generate a panda image") — respond with the CODE as text.
  Do NOT call generate_image in that case.
- You cannot edit files or run code here. For real coding tasks (editing a project,
  running commands), tell the user to use `ronin code "<task>"`.

The "no write access" rule applies only to their business data services, NOT to media generation.
Keep replies short; any media you generate is shown to the user automatically."""


def run_ask(config: CSKConfig, question: str, *, console: Console | None = None) -> AgentResultRich:
    """One-shot agent invocation.

    Modes:
    - Real key + any config: real provider + (real services or demo data).
    - No key + demo config: offline keyword-router 'demo brain' (no LLM call).
    """
    scan = InjectionScanner().scan(question)
    if scan.flagged:
        return AgentResultRich(
            success=False,
            output="[blocked] your question was flagged as a potential prompt-injection attempt.",
            iterations=0,
            error=f"injection-scan flagged: {[h['label'] for h in scan.hits]}",
            demo_mode=config.demo_mode,
        )

    tools = build_tools(config)

    if config.demo_mode and not _has_real_provider_key(config):
        result = demo_answer(question, tools)
        return AgentResultRich(
            success=result.success,
            output=result.output,
            iterations=result.iterations,
            trace=_serialize_trace(result.trace),
            usage={},
            error=result.error,
            demo_mode=True,
        )

    agent = _build_agent(config, tools)
    try:
        if console is not None:
            with Live(Spinner("dots", text="[cyan]thinking…[/cyan]"), console=console, refresh_per_second=10):
                result = agent.run(question)
        else:
            result = agent.run(question)
    except Exception as e:  # noqa: BLE001 — surface provider/network errors cleanly
        return AgentResultRich(
            success=False,
            output=_friendly_provider_error(e, config),
            iterations=0,
            error=f"{e.__class__.__name__}: {e}",
            demo_mode=config.demo_mode,
        )

    # Record token usage / cost (best-effort; never fail the user's command).
    try:
        from .usage import record_usage
        record_usage(
            command="ask",
            provider=config.provider,
            model=config.resolved_model(),
            input_tokens=int(result.usage.get("input_tokens", 0) or 0),
            output_tokens=int(result.usage.get("output_tokens", 0) or 0),
        )
    except Exception:  # noqa: BLE001
        pass

    return AgentResultRich(
        success=result.success,
        output=result.output,
        iterations=result.iterations,
        trace=_serialize_trace(result.trace),
        usage=result.usage,
        error=result.error,
        demo_mode=config.demo_mode,
    )


def start_chat(config: CSKConfig, *, console: Console, raw: bool = False) -> None:
    from ro_claude_kit_memory import ShortTermMemory

    from .media import build_media_tools, show_artifacts
    from .memory_store import build_remember_tool, load_memories, memory_prompt_block

    memory = ShortTermMemory(keep_recent=8, compress_threshold_tokens=6000)
    artifacts: list = []
    # Data + media tools + persistent cross-session memory (the remember tool),
    # all on one agent so a single plain-language request routes correctly.
    tools = build_tools(config) + build_media_tools(artifacts) + [build_remember_tool()]
    agent = _build_agent(config, tools, extra_system=CHAT_CAPABILITIES + memory_prompt_block())
    if load_memories():
        console.print(f"[dim]🧠 {len(load_memories())} thing(s) remembered about you[/dim]")

    while True:
        try:
            user = console.input("[bold cyan]you ›[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return
        if not user:
            continue
        if user in (":q", "/quit", "/exit"):
            console.print("[dim]bye[/dim]")
            return

        memory.add_turn("user", user)
        prior = "\n\n".join(f"{t.role.upper()}: {t.content}" for t in memory.turns[:-1]) or "(start of conversation)"
        prompt = f"Conversation so far:\n{prior}\n\nUser's new message: {user}"

        try:
            with Live(Spinner("dots", text="[cyan]thinking…[/cyan]"), console=console, refresh_per_second=10):
                result = agent.run(prompt)
        except Exception as e:  # noqa: BLE001 — never crash the chat on a provider/network error
            console.print(_friendly_provider_error(e, config) + "\n")
            memory.turns.pop()  # drop the unanswered user turn so context stays clean
            continue

        memory.add_turn("assistant", result.output)
        memory.maybe_compress()
        # auto-remember durable facts — only on substantive turns (saves rate limit)
        if len(user) > 20:
            from .memory_store import auto_extract_background
            auto_extract_background(config, f"USER: {user}\nASSISTANT: {result.output}")

        if raw:
            sys.stdout.write(result.output + "\n")
        else:
            from rich.markdown import Markdown
            console.print("[bold green]ronin ›[/bold green]")
            console.print(Markdown(result.output or "_(no output)_"))
            console.print()
        # Show any media the agent produced this turn (image inline, video opened).
        show_artifacts(artifacts)
