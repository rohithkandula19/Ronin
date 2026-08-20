"""The holdout eval hook: tool-call JSON validity — the number the SFT run is judged on.

The whole point of this SFT is protocol compliance, so the metric that decides whether an
adapter shipped is: **of the assistant turns that attempt a tool call, what fraction emit a
call the runtime can actually execute** — a known tool name (D1) with a JSON *object* for
arguments (D2). :data:`GATE` (``0.97``) is the bar, and it is checked on the **holdout**
tasks (never seen in training, :mod:`ronin_training.adapter.dataset`), because a validity
measured on the training tasks is a memorisation score.

This module is the offline, deterministic core of that hook: given the (name, arguments)
pairs a model produced on the holdout — parsed from its raw output by the provider, exactly
as the runtime parses them — it counts valid over attempted. The real gate runs the trained
model to produce those pairs on a GPU; the counting, the definition of "valid", and the
threshold are here and unit-tested, so the GPU step only supplies the completions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

#: The ship bar for tool-call validity on the holdout. Below this, the adapter is not
#: better than the base at the one thing it was trained to fix.
GATE: Final[float] = 0.97


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One attempted call, as the provider parsed it from the model's output."""

    name: str
    arguments: object


@dataclass(frozen=True, slots=True)
class ValidityReport:
    """Valid over attempted, with the denominator kept visible.

    ``rate`` is ``None`` when nothing attempted a call — an empty result is not a 100%
    pass, and reporting it as one is the failure this refuses (same rule as the eval
    suite's pass-rate-over-zero).
    """

    valid: int
    attempts: int

    def __post_init__(self) -> None:
        if self.valid > self.attempts or self.valid < 0:
            raise ValueError(f"impossible report: {self.valid} valid of {self.attempts}")

    @property
    def rate(self) -> float | None:
        return self.valid / self.attempts if self.attempts else None

    def meets_gate(self, gate: float = GATE) -> bool:
        """True iff something was attempted and its validity is at or above ``gate``."""
        return self.rate is not None and self.rate >= gate


def _default_known_tools() -> frozenset[str]:
    """The runtime's real tool names, loaded lazily so a test needn't touch the registry."""
    from ronin_training.validators import load_registry

    return frozenset(load_registry())


def call_is_valid(call: ToolCall, known: frozenset[str]) -> bool:
    """A call the runtime could execute: a known name (D1) with object arguments (D2)."""
    return call.name in known and isinstance(call.arguments, Mapping)


def calls_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[ToolCall]:
    """Every tool call in the assistant turns of ``{messages, tools}`` rows.

    This is how the *reference* completions are scored offline; the GPU hook scores the
    trained model's parsed calls through the same :func:`tool_validity`.
    """
    calls: list[ToolCall] = []
    for row in rows:
        for message in row.get("messages", []):
            if message.get("role") != "assistant":
                continue
            for raw in message.get("tool_calls") or []:
                function = raw.get("function", {})
                calls.append(ToolCall(name=function.get("name", ""), arguments=function.get("arguments")))
    return calls


def tool_validity(
    calls: Sequence[ToolCall], *, known_tools: frozenset[str] | None = None
) -> ValidityReport:
    """Count valid calls over attempted calls. ``known_tools`` defaults to the live registry."""
    known = known_tools if known_tools is not None else _default_known_tools()
    valid = sum(1 for call in calls if call_is_valid(call, known))
    return ValidityReport(valid=valid, attempts=len(calls))


__all__ = [
    "GATE",
    "ToolCall",
    "ValidityReport",
    "call_is_valid",
    "calls_from_rows",
    "tool_validity",
]
