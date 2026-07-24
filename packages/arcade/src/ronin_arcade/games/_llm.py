"""Shared LLM plumbing for the AI-powered arcade games.

These games route through ronin's **own** provider layer (``ronin_agent_patterns``
via ``runner.build_single_provider``), so they run on whatever backend the user
configured — Cerebras, Groq, Gemini, Claude, Ollama, OpenAI, … — rather than a
hardcoded vendor SDK. That keeps the arcade as provider-agnostic as the rest of
ronin.

Every LLM call is funneled through :func:`complete`, which never raises: on a
provider/network error it returns ``""`` so a game can degrade gracefully
instead of crashing. Call :func:`available` first to show a friendly "configure
a model" message when no backend is usable.
"""
from __future__ import annotations


def available() -> tuple[bool, str]:
    """Return ``(ok, reason)`` — whether an LLM backend is usable right now.

    ``ollama`` is treated as available (it's a local server, no key needed);
    every other provider needs a configured API key. ``reason`` is a short,
    user-facing explanation when ``ok`` is False.
    """
    try:
        from ronin_cli.config import load_config
        cfg = load_config()
    except Exception as e:  # noqa: BLE001 - missing/broken config shouldn't crash a game
        return False, f"couldn't load your ronin config ({e})"
    if cfg.provider == "ollama":
        return True, ""
    if cfg.key_for():
        return True, ""
    return False, "no model is configured — run `ronin login` or set a provider API key"


def complete(system: str, prompt: str, *, max_tokens: int = 400,
             history: list[tuple[str, str]] | None = None) -> str:
    """One provider-agnostic completion. Returns the model's text, or ``""`` on
    any error (so callers can fall back gracefully — never let a game crash on a
    rate-limit or dropped connection).

    ``history`` is an optional list of ``(role, content)`` turns (``role`` is
    ``"user"`` or ``"assistant"``) prepended before ``prompt``.
    """
    try:
        from ronin_agent_patterns import Message

        from ronin_cli.config import load_config
        from ronin_cli.runner import build_single_provider

        cfg = load_config()
        messages = [Message(role=r, content=c) for (r, c) in (history or [])]
        messages.append(Message(role="user", content=prompt))
        resp = build_single_provider(cfg).complete(
            system=system, messages=messages, tools=[], max_tokens=max_tokens)
        return (resp.text or "").strip()
    except Exception:  # noqa: BLE001 - provider/network/parse errors degrade to ""
        return ""
