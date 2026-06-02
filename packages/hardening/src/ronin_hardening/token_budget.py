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


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = PRICING.get(model)
    if rates is None:
        return 0.0
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
        self.used_cost_usd += estimate_cost_usd(self.model, input_tokens, output_tokens)
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
        if self.exhausted:
            raise BudgetExceededError(
                f"budget already exhausted (used={self.used_tokens} tokens, ${self.used_cost_usd:.4f})",
                used_tokens=self.used_tokens,
                used_cost_usd=self.used_cost_usd,
            )
