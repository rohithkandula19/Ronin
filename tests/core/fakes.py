"""Offline doubles for the three seams the loop is written against.

Each class satisfies its protocol *structurally* — no mocks, no monkeypatching,
no network, nothing imported from a provider. Every double **records what it was
handed**, because most of what a loop test needs to prove is about what the loop
*sent* (which messages, which specs, which approval), not merely what came back.

Two deliberate choices:

* ``FakeModel`` raises on an exhausted script rather than returning an empty
  stream. A silent empty stream would look like the "no final message" protocol
  error and make a mis-scripted test pass for the wrong reason.
* :func:`yield_to_loop` uses ``sleep(0)``, which is a *scheduling* yield and not
  a timed wait: it hands control to any sibling task, so an interleaving that
  *can* happen *will* happen. That makes concurrency assertions deterministic
  without putting the wall clock in the test's critical path.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ronin.core.protocols import FinalMessage, ModelChunk, TextChunk
from ronin.core.types import (
    AgentState,
    ApprovalDecision,
    Budget,
    DangerLevel,
    Message,
    Role,
    Text,
    ToolResult,
    ToolSpec,
    ToolUse,
)

#: Upper bound for a rendezvous that should complete immediately. It exists only
#: so a loop that serialized a batch fails the test instead of deadlocking it.
WATCHDOG_SECONDS = 5.0


async def yield_to_loop(times: int = 3) -> None:
    """Give sibling tasks a chance to run, without waiting on the clock."""
    for _ in range(times):
        await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# ModelClient
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ModelCall:
    """One recorded ``stream()`` invocation.

    ``messages`` is snapshotted into a tuple because the loop hands ``stream``
    its own *live* list: keeping the list would show later mutations and every
    recorded call would look identical.
    """

    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]

    @property
    def roles(self) -> tuple[Role, ...]:
        return tuple(message.role for message in self.messages)


class FakeModel:
    """A scripted provider: a list of responses, each a list of chunks."""

    def __init__(self, responses: Sequence[Sequence[ModelChunk]]) -> None:
        self._responses = [tuple(chunks) for chunks in responses]
        self.calls: list[ModelCall] = []

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ModelChunk]:
        self.calls.append(
            ModelCall(system=system, messages=tuple(messages), tools=tuple(tools))
        )
        if len(self.calls) > len(self._responses):
            raise AssertionError(
                f"FakeModel script exhausted: the loop asked for response "
                f"#{len(self.calls)} but only {len(self._responses)} were scripted"
            )
        return self._emit(self._responses[len(self.calls) - 1])

    @staticmethod
    async def _emit(chunks: Sequence[ModelChunk]) -> AsyncIterator[ModelChunk]:
        for chunk in chunks:
            yield chunk


# --------------------------------------------------------------------------- #
# ToolRegistry
# --------------------------------------------------------------------------- #

#: A tool body. Sync or async, so "a tool that raises" is expressible either way.
Handler = Callable[[Mapping[str, Any]], "ToolResult | Awaitable[ToolResult]"]


class FakeTools:
    """A registry of scripted handlers that records order *and* concurrency."""

    def __init__(self, tools: Mapping[str, tuple[ToolSpec, Handler]]) -> None:
        self._tools = dict(tools)
        #: Every call the loop actually handed to ``execute``, in start order.
        self.executions: list[ToolUse] = []
        #: Names in *completion* order, which differs from start order when the
        #: batch really ran concurrently.
        self.completions: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    @property
    def executed_names(self) -> tuple[str, ...]:
        return tuple(use.name for use in self.executions)

    def specs(self) -> Sequence[ToolSpec]:
        return tuple(spec for spec, _handler in self._tools.values())

    def get(self, name: str) -> ToolSpec | None:
        entry = self._tools.get(name)
        return entry[0] if entry is not None else None

    async def execute(self, use: ToolUse) -> ToolResult:
        # Recorded before the lookup so that an execution the loop should never
        # have attempted is still visible to the test, even though the loop's
        # boundary would otherwise convert the raise below into a ToolResult.
        self.executions.append(use)
        entry = self._tools.get(use.name)
        if entry is None:
            raise AssertionError(
                f"the loop executed {use.name!r}, which is not registered"
            )

        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            outcome = entry[1](use.arguments)
            if isinstance(outcome, ToolResult):
                return outcome
            return await outcome
        finally:
            self.in_flight -= 1
            self.completions.append(use.name)


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ApprovalAsk:
    """One recorded approval question, including what the human was shown."""

    spec: ToolSpec
    use: ToolUse
    rendered: str


ApprovalFn = Callable[[ToolSpec, ToolUse], ApprovalDecision]


class FakePolicy:
    """Approval, budget, and cancellation, each independently scriptable."""

    def __init__(
        self,
        *,
        approve_all: bool = True,
        approve: ApprovalFn | None = None,
        budget_reason: str | None = None,
        cancel_after: int | None = None,
        deny_reason: str = "policy denied it",
    ) -> None:
        self._approve_all = approve_all
        self._approve = approve
        self._budget_reason = budget_reason
        self._cancel_after = cancel_after
        self._deny_reason = deny_reason
        self.approvals: list[ApprovalAsk] = []
        self.budget_checks: list[Budget] = []
        self.cancel_polls = 0
        #: Settable at any moment — including from inside a tool handler, which
        #: is how a test cancels *mid-batch* without touching loop internals.
        self.cancel_now = False

    async def approve(
        self, spec: ToolSpec, use: ToolUse, *, rendered: str
    ) -> ApprovalDecision:
        self.approvals.append(ApprovalAsk(spec=spec, use=use, rendered=rendered))
        if self._approve is not None:
            return self._approve(spec, use)
        if self._approve_all:
            return ApprovalDecision(approved=True)
        return ApprovalDecision(approved=False, reason=self._deny_reason)

    def check_budget(self, budget: Budget) -> str | None:
        self.budget_checks.append(budget)
        return self._budget_reason

    def cancelled(self) -> bool:
        self.cancel_polls += 1
        if self._cancel_after is not None and self.cancel_polls > self._cancel_after:
            self.cancel_now = True
        return self.cancel_now

    def cancel(self) -> None:
        self.cancel_now = True


# --------------------------------------------------------------------------- #
# Script builders — terse, so a test reads as a script and not as plumbing
# --------------------------------------------------------------------------- #


def spec(
    name: str,
    *,
    danger: DangerLevel = DangerLevel.READ_ONLY,
    approval: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} (test double)",
        json_schema={"type": "object"},
        danger_level=danger,
        requires_approval=approval,
    )


def succeeds(content: str) -> Handler:
    def handler(_arguments: Mapping[str, Any]) -> ToolResult:
        return ToolResult(ok=True, content=content)

    return handler


def explodes(exc: BaseException) -> Handler:
    def handler(_arguments: Mapping[str, Any]) -> ToolResult:
        raise exc

    return handler


def call(id: str, name: str, **arguments: Any) -> ToolUse:
    return ToolUse(id=id, name=name, arguments=arguments)


def user(text: str) -> Message:
    return Message(role=Role.USER, content_blocks=(Text(text),))


def assistant_text(text: str) -> Message:
    return Message(role=Role.ASSISTANT, content_blocks=(Text(text),))


def assistant_calls(*calls: ToolUse, text: str = "") -> Message:
    blocks: tuple[Text | ToolUse, ...] = (Text(text),) if text else ()
    return Message(role=Role.ASSISTANT, content_blocks=blocks + calls)


def text_turn(*deltas: str) -> tuple[ModelChunk, ...]:
    """A response that streams prose and asks for nothing — ends the turn."""
    return (
        *(TextChunk(text=delta) for delta in deltas),
        FinalMessage(message=assistant_text("".join(deltas))),
    )


def tool_turn(*calls: ToolUse, text: str = "") -> tuple[ModelChunk, ...]:
    """A response whose final message asks for ``calls``."""
    return (FinalMessage(message=assistant_calls(*calls, text=text)),)


def seed(prompt: str = "read a.py") -> AgentState:
    """A minimal starting state with one user message."""
    return AgentState(messages=(user(prompt),))


__all__ = [
    "WATCHDOG_SECONDS",
    "ApprovalAsk",
    "ApprovalFn",
    "FakeModel",
    "FakePolicy",
    "FakeTools",
    "Handler",
    "ModelCall",
    "assistant_calls",
    "assistant_text",
    "call",
    "explodes",
    "seed",
    "spec",
    "succeeds",
    "text_turn",
    "tool_turn",
    "user",
    "yield_to_loop",
]
