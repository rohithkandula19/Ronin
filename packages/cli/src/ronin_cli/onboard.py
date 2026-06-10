"""First-run onboarding — a calm, guided setup instead of an error wall.

When ronin starts with no provider configured, this offers an inline picker:
choose a free provider, paste a key (masked), and you're coding. The provider
catalogue + config-building are pure and unit-tested; only the prompts touch IO.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProviderChoice:
    key: str            # provider id
    model: str | None   # default model (None → provider preset)
    blurb: str          # short descriptor
    where: str          # where to get a key
    needs_key: bool


# Free-first catalogue, best-for-newcomers order.
PROVIDERS: list[ProviderChoice] = [
    ProviderChoice("groq", "llama-3.3-70b-versatile", "free · fastest", "console.groq.com/keys", True),
    ProviderChoice("cerebras", "gpt-oss-120b", "free · very fast", "cloud.cerebras.ai", True),
    ProviderChoice("gemini", "gemini-flash-latest", "free tier", "aistudio.google.com/apikey", True),
    ProviderChoice("openrouter", None, "free models · one key", "openrouter.ai/keys", True),
    ProviderChoice("anthropic", "claude-sonnet-4-6", "top quality (paid)", "console.anthropic.com", True),
    ProviderChoice("ollama", "llama3.1", "local · no key needed", "ollama.com", False),
]


def build_config(base, choice: ProviderChoice, key: str | None):
    """Return a config switched to ``choice`` with ``key`` stored (if any). Pure."""
    cfg = base.model_copy(update={"provider": choice.key, "model": choice.model})
    if choice.needs_key and key:
        cfg.set_key_for(choice.key, key.strip())
    return cfg


def onboard(console, base):
    """Interactive first-run picker. Returns a configured RoninConfig, or None if
    the user skipped (caller then falls back to the help screen)."""
    from rich.prompt import Prompt
    from rich.text import Text

    from .config import save_config
    from .theme import ACCENT, MUTE, SOFT, gradient_text

    console.print()
    console.print(Text("  ") + gradient_text("ronin") + Text("  ·  let's get you set up", style=f"bold {SOFT}"))
    console.print(Text("  ronin runs FREE — pick a provider to start:", style=MUTE))
    console.print()
    for i, p in enumerate(PROVIDERS, 1):
        console.print(f"  [bold {ACCENT}]{i}[/bold {ACCENT}]  [bold]{p.key:<11}[/bold]"
                      f"[dim]{p.blurb:<22}[/dim] [#6b7089]→ {p.where}[/#6b7089]")
    console.print()
    pick = Prompt.ask(f"  [bold]Pick 1–{len(PROVIDERS)}[/bold] [dim](or Enter to skip)[/dim]", default="").strip()
    if not pick.isdigit() or not (1 <= int(pick) <= len(PROVIDERS)):
        return None
    choice = PROVIDERS[int(pick) - 1]

    key = None
    if choice.needs_key:
        console.print(f"\n  [dim]get a key at[/dim] [bold]{choice.where}[/bold]")
        key = Prompt.ask(f"  [bold]Paste your {choice.key} key[/bold]", password=True).strip()
        if not key:
            console.print("  [yellow]no key entered — skipping setup.[/yellow]")
            return None

    cfg = build_config(base, choice, key)
    try:
        save_config(cfg, scope="user")
    except OSError:
        pass
    console.print(f"\n  [green]✓ ready[/green] — [bold]{cfg.provider} · {cfg.resolved_model()}[/bold]. "
                  "Type a request to begin. [dim](/login to change later)[/dim]\n")
    return cfg
