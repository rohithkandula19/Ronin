"""Effort / reasoning-budget control — one knob across all providers.

ronin is provider-agnostic, but each provider exposes "think harder" through a
*different* native request parameter:

- **Anthropic** (Claude) — extended thinking, via a ``thinking`` block carrying a
  ``budget_tokens`` allowance the model may spend on internal reasoning before it
  answers.
- **OpenAI / OpenAI-compatible reasoning models** (o-series, gpt-5, …) — a single
  ``reasoning_effort`` string the API accepts as ``"low" | "medium" | "high"``.
- **Everyone else** (groq, cerebras, ollama, local/embedded, gemini-via-openai) —
  no reasoning knob at all, so the request body is left untouched.

This module is the single, PURE place that turns one user-facing level
(``low | medium | high | xhigh``) into the right native params for a given
provider. It does no I/O and imports nothing from the rest of the CLI, so it is
trivially unit-testable and safe to call on the hot LLM path.

Default behaviour is unchanged when no effort is set: callers only invoke
``effort_to_params`` when an effort level is actually configured, and even then a
no-knob provider (or ``low`` on Anthropic) yields an empty dict that merges into
the request body as a no-op.

Budget ladder (Anthropic ``budget_tokens``)
-------------------------------------------
The numbers below are deliberate, sane defaults — comfortably above Anthropic's
documented 1024-token minimum at ``medium`` and up, and well under typical
``max_tokens``, so the thinking budget never starves the visible answer:

    level   | reasoning_effort | Anthropic thinking budget_tokens
    --------|------------------|---------------------------------
    low     | "low"            | (disabled — no thinking block)
    medium  | "medium"         | 4096
    high    | "high"           | 8192
    xhigh   | "high" *clamped* | 16384

``xhigh`` is ronin's "max" tier. Anthropic can spend the larger 16384-token
budget natively; the OpenAI ``reasoning_effort`` enum only accepts up to
``"high"``, so ``xhigh`` is **clamped to "high"** there (documented on
``effort_to_params``). If Anthropic ever lowers the budget for a model, the only
knob to retune is ``_ANTHROPIC_BUDGET`` below.
"""
from __future__ import annotations

from dataclasses import dataclass


# Canonical, ordered effort levels (cheapest → most reasoning). Order matters:
# tests and any future "bump one level" logic rely on the index ascending.
EFFORT_LEVELS: list[str] = ["low", "medium", "high", "xhigh"]

# Friendly synonyms → canonical level. Keeps the CLI forgiving (``--effort med``,
# ``--effort hi``, ``--effort max`` all work) without leaking aliases elsewhere.
_ALIASES: dict[str, str] = {
    "low": "low",
    "lo": "low",
    "l": "low",
    "min": "low",
    "minimal": "low",
    "medium": "medium",
    "med": "medium",
    "m": "medium",
    "mid": "medium",
    "default": "medium",
    "high": "high",
    "hi": "high",
    "h": "high",
    "xhigh": "xhigh",
    "x-high": "xhigh",
    "extra": "xhigh",
    "extreme": "xhigh",
    "max": "xhigh",
    "maximum": "xhigh",
    "ultra": "xhigh",
}

# Anthropic extended-thinking budget per level. ``low`` maps to None → the
# thinking block is omitted entirely (extended thinking disabled), which is the
# cheapest path and matches "low = don't overthink it".
_ANTHROPIC_BUDGET: dict[str, int | None] = {
    "low": None,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
}

# OpenAI's ``reasoning_effort`` enum only accepts low/medium/high. ronin's extra
# ``xhigh`` tier is clamped down to "high" so the request stays valid.
_OPENAI_EFFORT: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",  # clamped — the API has no level above "high"
}

@dataclass(frozen=True)
class ReasoningCapability:
    """Provider feature declaration used by routing and request construction.

    ``preserve_tool_state`` means opaque provider metadata must survive a
    tool-call round trip. Gemini's thought signatures use this path through the
    OpenAI-compatible adapter even though Gemini has no normalized effort knob
    in that adapter.
    """

    provider: str
    request_mode: str  # "anthropic-thinking" | "openai-effort" | "none"
    preserve_tool_state: bool = False


_CAPABILITIES: dict[str, ReasoningCapability] = {
    "anthropic": ReasoningCapability("anthropic", "anthropic-thinking"),
    "openai": ReasoningCapability("openai", "openai-effort"),
    "gemini": ReasoningCapability("gemini", "none", preserve_tool_state=True),
}


def reasoning_capability(provider: str) -> ReasoningCapability:
    """Return the normalized reasoning/tool-state contract for a provider."""
    normalized = (provider or "").strip().lower()
    return _CAPABILITIES.get(normalized, ReasoningCapability(normalized or "unknown", "none"))

# One-line human descriptions, surfaced by ``/effort`` and ``describe_effort``.
_DESCRIPTIONS: dict[str, str] = {
    "low": "fastest, least reasoning — quick answers, minimal deliberation",
    "medium": "balanced reasoning budget for everyday coding tasks",
    "high": "deeper reasoning for tricky multi-step problems (slower, costlier)",
    "xhigh": "maximum reasoning budget — hardest problems, slowest, most tokens",
}


def normalize_effort(value: str | None) -> str | None:
    """Canonicalize a user-supplied effort string to an ``EFFORT_LEVELS`` member.

    Accepts the canonical names plus friendly synonyms (``med`` → ``medium``,
    ``hi`` → ``high``, ``max`` → ``xhigh``, …), case-insensitively and with
    surrounding whitespace stripped. Returns ``None`` for ``None``, empty/blank,
    or any unrecognized value — so an invalid ``--effort`` simply disables the
    feature rather than crashing the call path.
    """
    if not value:
        return None
    key = value.strip().lower()
    if not key:
        return None
    return _ALIASES.get(key)


def effort_to_params(provider: str, effort: str) -> dict:
    """Map an effort level to ``provider``'s native reasoning request params.

    ``effort`` must be a canonical ``EFFORT_LEVELS`` member (run it through
    ``normalize_effort`` first); an unknown level yields ``{}`` so a bad value is
    inert on the call path.

    Returns a dict to **merge into the provider's request body**:

    - ``anthropic`` → ``{"thinking": {"type": "enabled", "budget_tokens": N}}``
      where N grows with effort (4096 / 8192 / 16384 for medium / high / xhigh).
      ``low`` returns ``{}`` — extended thinking stays disabled, the cheapest path.
    - ``openai`` (and any OpenAI-compatible *reasoning* model) →
      ``{"reasoning_effort": e}`` with ``e`` in ``low|medium|high``. ronin's extra
      ``xhigh`` tier is **clamped to "high"** (the OpenAI enum has no higher value).
    - any other provider (``groq``, ``cerebras``, ``ollama``, ``local``,
      ``gemini``, ``together``, ``fireworks``, ``openrouter``, ``custom``) → ``{}``
      — those models have no reasoning-budget knob, so the body is untouched.

    The empty-dict cases are exactly what keeps default behaviour unchanged: a
    no-knob provider, or ``low`` on Anthropic, contributes nothing to the body.
    """
    effort = (effort or "").strip().lower()
    if effort not in EFFORT_LEVELS:
        return {}

    provider = (provider or "").strip().lower()

    capability = reasoning_capability(provider)
    if capability.request_mode == "anthropic-thinking":
        budget = _ANTHROPIC_BUDGET.get(effort)
        if budget is None:  # low → leave extended thinking disabled
            return {}
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}

    if capability.request_mode == "openai-effort":
        return {"reasoning_effort": _OPENAI_EFFORT[effort]}

    # groq / cerebras / ollama / local / gemini / together / fireworks /
    # openrouter / custom — no native reasoning knob; leave the body alone.
    return {}


def describe_effort(effort: str) -> str:
    """One-line human description of an effort level (for ``/effort`` + help).

    Falls back to a generic line for an unknown level rather than raising."""
    effort = (effort or "").strip().lower()
    return _DESCRIPTIONS.get(effort, f"effort level {effort!r}")
