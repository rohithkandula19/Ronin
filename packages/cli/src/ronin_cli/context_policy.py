"""Provider-aware context-window budgets for Ronin's agent surfaces.

Provider APIs do not expose a uniform, trustworthy context-limit handshake, so
this module keeps a conservative local catalog and lets an operator override it
per project. The policy reserves output space and compacts before the hard
window, rather than waiting for a provider-side context-length failure.
"""
from __future__ import annotations

from dataclasses import dataclass


MIN_CONTEXT_WINDOW = 4_096
MAX_CONTEXT_WINDOW = 2_000_000
FALLBACK_CONTEXT_WINDOW = 32_000

# These patterns cover Ronin's presets. Unknown hosted, local, and routed models
# deliberately use the conservative fallback until the operator sets an override.
_MODEL_WINDOWS: tuple[tuple[str, int], ...] = (
    ("claude-", 200_000),
    ("gemini-", 1_000_000),
    ("gpt-4.1", 1_000_000),
    ("gpt-4o", 128_000),
    ("gpt-oss", 128_000),
    ("llama3.1", 128_000),
    ("llama-3.3", 128_000),
)
_PROVIDER_WINDOWS: dict[str, int] = {
    "anthropic": 200_000,
    "cerebras": 128_000,
    "gemini": 1_000_000,
    "groq": 128_000,
    "openai": 128_000,
}


@dataclass(frozen=True)
class ContextWindowPolicy:
    """The bounded input budget applied consistently across a Ronin turn."""

    window_tokens: int
    input_budget_tokens: int
    output_reserve_tokens: int
    compaction_threshold_tokens: int
    retrieval_budget_tokens: int
    source: str
    fast: bool = False

    def used_percent(self, used_tokens: int) -> int:
        """Return bounded input-window utilization for terminal and TUI gauges."""
        return min(100, max(0, int(max(0, used_tokens) * 100 / self.input_budget_tokens)))

    def remaining_percent(self, used_tokens: int) -> int:
        return 100 - self.used_percent(used_tokens)


def parse_context_window(value: str) -> int:
    """Parse a human-friendly context override such as ``64k`` or ``1m``."""
    raw = value.strip().lower().replace("_", "")
    multiplier = 1
    if raw.endswith("k"):
        raw, multiplier = raw[:-1], 1_000
    elif raw.endswith("m"):
        raw, multiplier = raw[:-1], 1_000_000
    if not raw.isdigit():
        raise ValueError("context window must be a whole token count such as 64000, 64k, or 1m")
    tokens = int(raw) * multiplier
    _validate_window(tokens)
    return tokens


def resolve_context_policy(config) -> ContextWindowPolicy:
    """Resolve the configured model into one guarded context-window policy."""
    override = getattr(config, "context_window", None)
    if override is not None:
        window, source = int(override), "config override"
        _validate_window(window)
    else:
        provider = str(getattr(config, "provider", "")).strip().lower()
        model = str(config.resolved_model() if hasattr(config, "resolved_model") else "").strip().lower()
        matched = next((tokens for prefix, tokens in _MODEL_WINDOWS if model.startswith(prefix)), None)
        if matched is not None:
            window, source = matched, "model catalog"
        elif provider in _PROVIDER_WINDOWS:
            window, source = _PROVIDER_WINDOWS[provider], "provider fallback"
        else:
            window, source = FALLBACK_CONTEXT_WINDOW, "conservative fallback"

    output_reserve = min(16_384, max(1_024, window // 8))
    input_budget = max(1_024, window - output_reserve)
    compact_at = max(2_048, int(input_budget * 0.75))
    fast = bool(getattr(config, "fast", False))
    if fast:
        compact_at = min(compact_at, 16_000)
    retrieval = min(8_000, max(1_024, input_budget // 16))
    return ContextWindowPolicy(
        window_tokens=window,
        input_budget_tokens=input_budget,
        output_reserve_tokens=output_reserve,
        compaction_threshold_tokens=compact_at,
        retrieval_budget_tokens=retrieval,
        source=source,
        fast=fast,
    )


def _validate_window(tokens: int) -> None:
    if not MIN_CONTEXT_WINDOW <= tokens <= MAX_CONTEXT_WINDOW:
        raise ValueError(
            f"context window must be between {MIN_CONTEXT_WINDOW:,} and {MAX_CONTEXT_WINDOW:,} tokens",
        )
