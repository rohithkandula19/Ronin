"""See the agent loop work: ``uv run python -m ronin.core.demo``.

No model, no network, no key, no cost. The model is a script and the tools are
closures, both defined here — ``core`` may not import ``providers`` or ``tools``
(``docs/ARCHITECTURE.md`` §3), and that constraint is what makes this demo possible
at all: the loop takes its model, tools and policy as injected protocols, so a
scripted stand-in is not a mock of the real thing, it *is* one of the things it
accepts.

Seven scenes, each one a property the loop is supposed to have:

1. prose streams as ``TextDelta`` and a turn with no tool calls ends ``DONE``
2. read-only calls run in parallel; anything mutating runs serially
3. an approval-gated tool is asked about, and a refusal comes back as a value
4. a tool that raises becomes a ``ToolResult``, not an exception
5. the budget stops the turn before the model is called again
6. **the stall detector**: the same call three times earns a nudge, again aborts
7. an interrupt leaves a resumable state and answers every outstanding call

Scene 6 is the one worth watching. Everything else here is plumbing; a loop that
cannot notice it is repeating itself will happily read the same file forty times and
bill you for it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from .loop import STALL_REPEATS, StalledError, StopReason, run_turn
from .protocols import FinalMessage, ModelChunk, TextChunk
from .types import (
    AgentState,
    ApprovalDecision,
    ApprovalRequest,
    Budget,
    DangerLevel,
    Event,
    Message,
    Role,
    Text,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolSpec,
    ToolStart,
    ToolUse,
    TurnEnd,
)

WIDTH = 78


def rule(title: str) -> None:
    print(f"\n{'─' * WIDTH}\n{title}\n{'─' * WIDTH}")


def note(text: str) -> None:
    print(f"    {text}")


# --------------------------------------------------------------------------- #
# The three injected collaborators, scripted
# --------------------------------------------------------------------------- #


class ScriptedModel:
    """A ``ModelClient`` that replays a fixed list of responses, in order."""

    def __init__(self, responses: Sequence[Sequence[ModelChunk]]) -> None:
        self._responses = [tuple(chunks) for chunks in responses]
        self.calls = 0

    def stream(
        self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ModelChunk]:
        self.calls += 1
        index = min(self.calls - 1, len(self._responses) - 1)
        return self._emit(self._responses[index])

    @staticmethod
    async def _emit(chunks: Sequence[ModelChunk]) -> AsyncIterator[ModelChunk]:
        for chunk in chunks:
            yield chunk


Handler = Callable[[Mapping[str, Any]], ToolResult]


class ScriptedTools:
    """A ``ToolRegistry`` of closures that records concurrency as it goes."""

    def __init__(self, tools: Mapping[str, tuple[ToolSpec, Handler]]) -> None:
        self._tools = dict(tools)
        self.in_flight = 0
        self.max_in_flight = 0

    def specs(self) -> Sequence[ToolSpec]:
        return tuple(spec for spec, _ in self._tools.values())

    def get(self, name: str) -> ToolSpec | None:
        entry = self._tools.get(name)
        return entry[0] if entry is not None else None

    async def execute(self, use: ToolUse) -> ToolResult:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            # A real await, so concurrent calls genuinely overlap and
            # `max_in_flight` measures something.
            await asyncio.sleep(0.01)
            return self._tools[use.name][1](use.arguments)
        finally:
            self.in_flight -= 1


class ScriptedPolicy:
    """A ``Policy`` with each of its three answers independently settable."""

    def __init__(
        self,
        *,
        approve: bool = True,
        budget_reason: str | None = None,
        cancel_after_polls: int | None = None,
    ) -> None:
        self._approve = approve
        self._budget_reason = budget_reason
        self._cancel_after = cancel_after_polls
        self.polls = 0
        self.asked: list[str] = []

    async def approve(self, spec: ToolSpec, use: ToolUse, *, rendered: str) -> ApprovalDecision:
        self.asked.append(rendered)
        if self._approve:
            return ApprovalDecision(approved=True)
        return ApprovalDecision(approved=False, reason="the human said no")

    def check_budget(self, budget: Budget) -> str | None:
        return self._budget_reason

    def cancelled(self) -> bool:
        self.polls += 1
        return self._cancel_after is not None and self.polls > self._cancel_after


# --------------------------------------------------------------------------- #
# Script helpers
# --------------------------------------------------------------------------- #


def tool(
    name: str, *, danger: DangerLevel = DangerLevel.READ_ONLY, approval: bool = False
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} (demo)",
        json_schema={"type": "object"},
        danger_level=danger,
        requires_approval=approval,
    )


def returns(content: str) -> Handler:
    def handler(_arguments: Mapping[str, Any]) -> ToolResult:
        return ToolResult(ok=True, content=content)

    return handler


def raises(exc: Exception) -> Handler:
    def handler(_arguments: Mapping[str, Any]) -> ToolResult:
        raise exc

    return handler


def calls(*uses: ToolUse) -> tuple[ModelChunk, ...]:
    return (FinalMessage(message=Message(role=Role.ASSISTANT, content_blocks=uses)),)


def says(*deltas: str) -> tuple[ModelChunk, ...]:
    joined = "".join(deltas)
    return (
        *(TextChunk(text=delta) for delta in deltas),
        FinalMessage(message=Message(role=Role.ASSISTANT, content_blocks=(Text(joined),))),
    )


def use(id: str, name: str, **arguments: Any) -> ToolUse:
    return ToolUse(id=id, name=name, arguments=arguments)


def start(prompt: str) -> AgentState:
    return AgentState(messages=(Message(role=Role.USER, content_blocks=(Text(prompt),)),))


async def show(events: AsyncIterator[Event]) -> list[Event]:
    """Print each event as it arrives, the way any consumer would, and collect them.

    This is the event bus contract in eight lines: the loop yields, a consumer
    renders. The loop itself prints nothing.
    """
    collected: list[Event] = []
    async for event in events:
        collected.append(event)
        if isinstance(event, TextDelta):
            print(f"    …{event.text!r}")
        elif isinstance(event, ToolStart):
            print(f"    → {event.name}({dict(event.arguments)})")
        elif isinstance(event, ToolEnd):
            mark = "✔" if event.result.ok else "✗"
            detail = event.result.content if event.result.ok else event.result.error
            print(f"    {mark} {event.name}: {detail[:60]}")
        elif isinstance(event, ApprovalRequest):
            print(f"    ? approval for {event.name}: {event.rendered}")
        elif isinstance(event, TurnEnd):
            print(f"    ■ {event.state.value} ({event.stop_reason})")
    return collected


# --------------------------------------------------------------------------- #
# Scenes
# --------------------------------------------------------------------------- #


async def scene_text_only() -> None:
    rule("1. prose streams, and a turn that asks for nothing ends DONE")
    model = ScriptedModel([says("I read it. ", "Nothing to change.")])
    await show(run_turn(start("look at a.py"), model, ScriptedTools({}), ScriptedPolicy()))
    note(f"stop reason: {StopReason.NO_TOOL_CALLS.value} — one model call, no tools")


async def scene_parallel_then_serial() -> None:
    rule("2. read-only runs in parallel; one mutating call forces serial")
    read_only = ScriptedTools(
        {"read_a": (tool("read_a"), returns("a")), "read_b": (tool("read_b"), returns("b"))}
    )
    model = ScriptedModel([calls(use("t1", "read_a"), use("t2", "read_b")), says("done")])
    await show(run_turn(start("read both"), model, read_only, ScriptedPolicy()))
    note(f"max concurrent: {read_only.max_in_flight} (both read-only → parallel)")

    mixed = ScriptedTools(
        {
            "read_a": (tool("read_a"), returns("a")),
            "write_b": (tool("write_b", danger=DangerLevel.MUTATING), returns("wrote b")),
        }
    )
    model = ScriptedModel([calls(use("t1", "read_a"), use("t2", "write_b")), says("done")])
    await show(run_turn(start("read then write"), model, mixed, ScriptedPolicy()))
    note(f"max concurrent: {mixed.max_in_flight} (one mutating → serial, in call order)")


async def scene_approval() -> None:
    rule("3. an approval-gated tool is asked about — both answers shown")

    def gated() -> ScriptedTools:
        return ScriptedTools(
            {"rm": (tool("rm", danger=DangerLevel.DESTRUCTIVE, approval=True), returns("deleted"))}
        )

    granted = ScriptedPolicy(approve=True)
    model = ScriptedModel([calls(use("t1", "rm", path="build/")), says("removed it")])
    await show(run_turn(start("delete build/"), model, gated(), granted))
    note(f"approved, and the human was shown exactly: {granted.asked[0]!r}")
    note("what is shown is what runs — the rendered text is in the event, not re-derived")

    refused = ScriptedPolicy(approve=False)
    model = ScriptedModel([calls(use("t1", "rm", path="build/")), says("understood, leaving it")])
    await show(run_turn(start("delete build/"), model, gated(), refused))
    note("refused: the model was told DENIED and continued — the run did not end")


async def scene_tool_raises() -> None:
    rule("4. a tool that raises becomes a ToolResult, never an exception")
    tools = ScriptedTools({"read_a": (tool("read_a"), raises(FileNotFoundError("a.py")))})
    model = ScriptedModel([calls(use("t1", "read_a")), says("the file is missing")])
    await show(run_turn(start("read a.py"), model, tools, ScriptedPolicy()))
    note("errors are values across the tool boundary, so the model can react")


async def scene_budget() -> None:
    rule("5. the budget stops the turn before the model is called again")
    model = ScriptedModel([says("never reached")])
    policy = ScriptedPolicy(budget_reason=StopReason.COST_BUDGET.value)
    await show(run_turn(start("expensive thing"), model, ScriptedTools({}), policy))
    note(f"model calls made: {model.calls} — the check happens first, so nothing was spent")


async def scene_stall() -> None:
    rule(f"6. THE STALL DETECTOR — same call {STALL_REPEATS}x earns a nudge, again aborts")
    tools = ScriptedTools({"read_a": (tool("read_a"), returns("same contents every time"))})
    # The same fingerprint forever: identical name, identical arguments.
    stuck = calls(use("t1", "read_a", path="a.py"))
    model = ScriptedModel([stuck])
    try:
        await show(run_turn(start("fix the bug"), model, tools, ScriptedPolicy()))
    except StalledError as exc:
        note(f"StalledError: {exc}")
        note(f"fingerprint: {exc.fingerprint}")
        # `agent_state`, not `state`: the abort is a checkpoint, and the attribute is
        # named for what it carries. Throwing the transcript away would make the one
        # failure mode users most want to inspect the one they cannot.
        aborted = exc.agent_state
        answered = (
            0 if aborted is None else sum(len(message.tool_results) for message in aborted.messages)
        )
        note(f"agent_state survives the abort, with {answered} answered call(s)")
        note("the nudge naming the repetition was appended before the abort:")
        if aborted is not None:
            for message in aborted.messages:
                if message.metadata.get("kind") == "stall_nudge":
                    note(f'  "{message.text}"')
    else:  # pragma: no cover - the script cannot escape the stall
        note("BUG: the loop did not detect the stall")


async def scene_interrupt() -> None:
    rule("7. an interrupt answers every outstanding call and stays resumable")
    tools = ScriptedTools(
        {"read_a": (tool("read_a"), returns("a")), "read_b": (tool("read_b"), returns("b"))}
    )
    model = ScriptedModel([calls(use("t1", "read_a"), use("t2", "read_b")), says("stopped")])
    # Polls: 1 = top of turn, 2 = approving the first call → flip after the second.
    events = await show(
        run_turn(start("read both"), model, tools, ScriptedPolicy(cancel_after_polls=2))
    )
    ends = [event for event in events if isinstance(event, TurnEnd)]
    final = ends[-1].agent_state
    answered = 0 if final is None else sum(len(message.tool_results) for message in final.messages)
    note(f"tool results in the transcript: {answered} — every id answered, so a provider")
    note("would accept this state on resume. That is what 'resumable' has to mean.")


async def main() -> int:
    await scene_text_only()
    await scene_parallel_then_serial()
    await scene_approval()
    await scene_tool_raises()
    await scene_budget()
    await scene_stall()
    await scene_interrupt()
    rule("done")
    print("Nothing above touched a network, a model or a key. The loop takes its")
    print("model, tools and policy as protocols, which is why this file can be a")
    print("complete demonstration of it in one process with no configuration.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
