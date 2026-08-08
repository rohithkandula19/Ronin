"""The three seams the loop is written against — injected, never imported.

The loop knows *these protocols* and nothing else. It has no idea which provider
is answering, which tools exist, or who approves an action, which is what makes
it testable with a scripted fake and no network.

Errors are values across the tool boundary: :meth:`ToolRegistry.execute` returns
a ``ToolResult`` rather than raising. The loop still guards the call, because a
protocol cannot force a third-party implementation to behave.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .types import (
    ApprovalDecision,
    Budget,
    Message,
    ToolResult,
    ToolSpec,
    ToolUse,
)

# --------------------------------------------------------------------------- #
# What a model streams back
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TextChunk:
    """An incremental piece of the model's output."""

    text: str
    thinking: bool = False


@dataclass(frozen=True, slots=True)
class ResetChunk:
    """The provider re-streamed this turn; discard what was emitted so far."""

    reason: str = ""


@dataclass(frozen=True, slots=True)
class FinalMessage:
    """The completed assistant message, with any tool calls it decided on.

    ``usage`` is provider-reported and additive: the loop folds it into the
    budget. Absent keys mean "not reported", never zero-cost.
    """

    message: Message
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


#: One item from a model stream. Exactly one ``FinalMessage`` ends a stream.
ModelChunk = TextChunk | ResetChunk | FinalMessage


@runtime_checkable
class ModelClient(Protocol):
    """A provider adapter, reduced to the one thing the loop needs.

    ``system`` is a separate argument rather than a message, because that is the
    lowest common denominator: adapters whose API takes a system *parameter* use
    it directly, and adapters whose API wants it in the array prepend it. A
    system-role message inside ``messages`` is legal and it is the adapter's job
    to render it — the loop stays provider-agnostic either way.
    """

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ModelChunk]: ...


@runtime_checkable
class ToolRegistry(Protocol):
    """The tool layer, reduced to lookup and execution."""

    def specs(self) -> Sequence[ToolSpec]:
        """Every tool the model may call this turn."""
        ...

    def get(self, name: str) -> ToolSpec | None:
        """The spec for ``name``, or ``None`` if the model hallucinated it."""
        ...

    async def execute(self, use: ToolUse) -> ToolResult:
        """Run one call. Returns a result; failure is a value, not an exception."""
        ...


@runtime_checkable
class Policy(Protocol):
    """Approval, budget, and cancellation — the three things that stop a turn.

    Approval lives here rather than in the event stream because the work order
    injects it: the loop asks, the policy answers. ``ApprovalRequest`` is still
    emitted so a UI can show what is being decided, but it is *informational* —
    a consumer never replies through the stream (see ``docs/ARCHITECTURE.md`` §4).
    """

    async def approve(
        self, spec: ToolSpec, use: ToolUse, *, rendered: str
    ) -> ApprovalDecision:
        """Decide one gated call. Must return a decision, never hang forever."""
        ...

    def check_budget(self, budget: Budget) -> str | None:
        """``None`` to continue, else a stop reason (``"token_budget"``, …)."""
        ...

    def cancelled(self) -> bool:
        """Whether the user has asked to stop. Polled at every await point."""
        ...
