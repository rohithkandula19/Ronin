"""Cost accounting for the Cost Router — what a turn *would* cost, and what
routing to a cheaper/free blade actually saved.

Prices are USD per 1M tokens (input, output), best-effort and easy to edit.
Free-tier providers (gemini/cerebras/groq/openrouter-free/ollama) are $0 — the
whole point of ronin is that most turns can run on them for nothing. Anything
unknown falls back to a conservative mid estimate so we never under-report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# (input $/Mtok, output $/Mtok). Keyed by provider; per-model overrides win.
_PROVIDER_PRICE: dict[str, tuple[float, float]] = {
    "anthropic": (3.0, 15.0),     # sonnet-class
    "openai": (0.15, 0.60),       # 4o-mini-class default
    "gemini": (0.0, 0.0),         # free tier
    "cerebras": (0.0, 0.0),       # free tier
    "groq": (0.0, 0.0),           # free tier
    "openrouter": (0.0, 0.0),     # default model is a :free one
    "ollama": (0.0, 0.0),         # local
    "together": (0.88, 0.88),
    "fireworks": (0.9, 0.9),
    "custom": (0.5, 0.5),
}

# Per-model overrides (substring match on the model name, first hit wins).
_MODEL_PRICE: list[tuple[str, tuple[float, float]]] = [
    ("claude-opus", (15.0, 75.0)),
    ("claude-sonnet", (3.0, 15.0)),
    ("claude-haiku", (0.80, 4.0)),
    ("gpt-4o-mini", (0.15, 0.60)),
    ("gpt-4o", (2.5, 10.0)),
    (":free", (0.0, 0.0)),
]

_FALLBACK = (1.0, 3.0)


def price_for(provider: str, model: str | None) -> tuple[float, float]:
    """(input, output) $/Mtok for a provider+model."""
    if model:
        low = model.lower()
        for frag, price in _MODEL_PRICE:
            if frag in low:
                return price
    if provider in _PROVIDER_PRICE:
        return _PROVIDER_PRICE[provider]
    return _FALLBACK


def estimate_cost(provider: str, model: str | None, in_tok: int, out_tok: int,
                  cached_tok: int = 0) -> float:
    """USD cost of a turn. ``cached_tok`` (of the ``in_tok``) are prompt-cache
    reads, billed at ~10% of the input rate."""
    pin, pout = price_for(provider, model)
    cached = max(0, min(cached_tok, in_tok))
    fresh_in = in_tok - cached
    return ((fresh_in / 1_000_000) * pin
            + (cached / 1_000_000) * pin * 0.10
            + (out_tok / 1_000_000) * pout)


def is_free(provider: str, model: str | None) -> bool:
    return price_for(provider, model) == (0.0, 0.0)


@dataclass
class CostLedger:
    """Accumulates what the session actually spent and what routing saved versus
    a baseline (the strong provider/model run on every turn)."""
    baseline_provider: str = "anthropic"
    baseline_model: str | None = "claude-sonnet-4-6"
    spent: float = 0.0
    baseline_spent: float = 0.0
    turns: int = 0
    free_turns: int = 0
    last_actual: float = 0.0     # most recent turn's cost / baseline — for persistence
    last_baseline: float = 0.0
    _by_provider: dict[str, float] = field(default_factory=dict)

    def record(self, provider: str, model: str | None, in_tok: int, out_tok: int,
               cached_tok: int = 0) -> None:
        actual = estimate_cost(provider, model, in_tok, out_tok, cached_tok)
        baseline = estimate_cost(self.baseline_provider, self.baseline_model, in_tok, out_tok)
        self.last_actual, self.last_baseline = actual, baseline
        self.spent += actual
        self.baseline_spent += baseline
        self.turns += 1
        if is_free(provider, model):
            self.free_turns += 1
        key = f"{provider}:{model}" if model else provider
        self._by_provider[key] = self._by_provider.get(key, 0.0) + actual

    @property
    def saved(self) -> float:
        """How much the routing saved vs running the strong model every turn."""
        return max(0.0, self.baseline_spent - self.spent)

    def summary(self) -> str:
        """One-line session summary for the status line / session end."""
        if self.turns == 0:
            return "cost: $0.00"
        return (f"cost: ${self.spent:.4f} · saved ${self.saved:.4f} vs all-"
                f"{self.baseline_provider} · {self.free_turns}/{self.turns} turns free")
