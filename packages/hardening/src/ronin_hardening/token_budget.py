"""Stop an agent run before it burns more than ``budget_tokens`` total.

Lesson 6 of PRODUCTION_LESSONS: an iteration cap protects you from infinite
loops, not from expensive ones. ``TokenBudget`` is the missing primitive.

Usage:

    from ronin_hardening import TokenBudget, BudgetExceededError

    budget = TokenBudget(max_tokens=20_000, max_cost_usd=0.50, model="claude-sonnet-4-6")
    try:
        result = agent.run(question, budget=budget)
    except BudgetExceededError as exc:
        log.warning("agent halted: %s", exc)

Or wrap the loop yourself — call ``budget.charge(input, output)`` after each
model invocation; it raises ``BudgetExceededError`` once the cap is crossed.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# Cost in USD per 1M tokens — same table as ronin_cli.usage.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7":      {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6":    {"input": 3.0,  "output": 15.0},
    "claude-haiku-4-5":     {"input": 0.80, "output": 4.0},
    "gpt-4o":               {"input": 2.50, "output": 10.0},
    "gpt-4o-mini":          {"input": 0.15, "output": 0.60},
}


# Models whose pricing is known to be $0 (keyless/local + free-tier defaults).
# Kept explicit so a cost cap does NOT fail closed on a genuinely-free model.
FREE_TIER_MODELS: frozenset[str] = frozenset({
    "ollama", "local",
    "gpt-oss-120b", "zai-glm-4.7",
    "gemini-flash-latest", "gemini-1.5-flash",
    "llama3.1", "llama3",
})


def pricing_known(model: str) -> bool:
    """True if we can price ``model`` — a real paid rate or a known-$0 free model."""
    low = (model or "").lower()
    return model in PRICING or model in FREE_TIER_MODELS or ":free" in low


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """USD cost for a call, or ``None`` when pricing is UNKNOWN.

    Returning ``None`` (never a silent ``0.0``) is the safety-critical bit: an
    unknown model must not be treated as free, which would let a ``max_cost_usd``
    cap pass forever. Known-$0 free/local models return ``0.0``.
    """
    low = (model or "").lower()
    if model in FREE_TIER_MODELS or ":free" in low:
        return 0.0
    rates = PRICING.get(model)
    if rates is None:
        return None
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


class BudgetExceededError(Exception):
    """Raised when a budget cap is crossed mid-run."""

    def __init__(self, message: str, *, used_tokens: int, used_cost_usd: float):
        super().__init__(message)
        self.used_tokens = used_tokens
        self.used_cost_usd = used_cost_usd


class TokenBudget(BaseModel):
    """Running token / cost counter with hard caps.

    ``max_tokens`` and ``max_cost_usd`` are both optional; set what you care
    about. If both are set, whichever crosses first triggers the abort.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    max_tokens: int | None = None
    max_cost_usd: float | None = None
    model: str = "claude-sonnet-4-6"  # used for cost estimation
    # Explicit opt-in to run a $ cap when pricing is UNKNOWN. Default False =
    # fail closed rather than silently let an unpriced model run uncapped.
    allow_unknown_pricing: bool = False

    used_input_tokens: int = 0
    used_output_tokens: int = 0
    used_cost_usd: float = 0.0
    call_count: int = 0

    @property
    def used_tokens(self) -> int:
        return self.used_input_tokens + self.used_output_tokens

    @property
    def remaining_tokens(self) -> int | None:
        return None if self.max_tokens is None else self.max_tokens - self.used_tokens

    @property
    def remaining_cost_usd(self) -> float | None:
        return None if self.max_cost_usd is None else self.max_cost_usd - self.used_cost_usd

    @property
    def exhausted(self) -> bool:
        if self.max_tokens is not None and self.used_tokens >= self.max_tokens:
            return True
        if self.max_cost_usd is not None and self.used_cost_usd >= self.max_cost_usd:
            return True
        return False

    def charge(self, input_tokens: int, output_tokens: int) -> None:
        """Record one model call's usage. Raises ``BudgetExceededError`` on cross."""
        self.used_input_tokens += max(0, int(input_tokens))
        self.used_output_tokens += max(0, int(output_tokens))
        cost = estimate_cost_usd(self.model, input_tokens, output_tokens)
        if cost is None:
            # UNKNOWN pricing — never treat as $0.
            if self.max_cost_usd is not None and not self.allow_unknown_pricing:
                raise BudgetExceededError(
                    f"cost budget cannot be enforced: pricing is UNKNOWN for model "
                    f"'{self.model}'. Use a model with known pricing, or set "
                    f"allow_unknown_pricing=True to run without cost enforcement.",
                    used_tokens=self.used_tokens,
                    used_cost_usd=self.used_cost_usd,
                )
            cost = 0.0  # allowed, or no cost cap set — the token cap still applies below
        self.used_cost_usd += cost
        self.call_count += 1

        if self.max_tokens is not None and self.used_tokens > self.max_tokens:
            raise BudgetExceededError(
                f"token budget exceeded: {self.used_tokens} > {self.max_tokens}",
                used_tokens=self.used_tokens,
                used_cost_usd=self.used_cost_usd,
            )
        if self.max_cost_usd is not None and self.used_cost_usd > self.max_cost_usd:
            raise BudgetExceededError(
                f"cost budget exceeded: ${self.used_cost_usd:.4f} > ${self.max_cost_usd:.4f}",
                used_tokens=self.used_tokens,
                used_cost_usd=self.used_cost_usd,
            )

    def check_before(self) -> None:
        """Raise if we shouldn't even start the next call."""
        if (self.max_cost_usd is not None and not self.allow_unknown_pricing
                and not pricing_known(self.model)):
            # Fail closed up front: a cost cap over an unpriced model is unenforceable.
            raise BudgetExceededError(
                f"cost budget cannot be enforced: pricing is UNKNOWN for model "
                f"'{self.model}'. Use a model with known pricing, or set "
                f"allow_unknown_pricing=True to run without cost enforcement.",
                used_tokens=self.used_tokens,
                used_cost_usd=self.used_cost_usd,
            )
        if self.exhausted:
            raise BudgetExceededError(
                f"budget already exhausted (used={self.used_tokens} tokens, ${self.used_cost_usd:.4f})",
                used_tokens=self.used_tokens,
                used_cost_usd=self.used_cost_usd,
            )
