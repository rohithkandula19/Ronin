"""A runnable, fully offline demo of the ReAct loop.

    uv run python -m ronin.demo

No network, no API key, no provider: the model is a scripted stand-in defined
right here, so what you are watching is the *loop's* behaviour. It walks through
the four things worth seeing in one run — parallel read-only tools, an approval
denial, a deterministically truncated result, and the stall nudge — then prints
the final resumable state.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Sequence

from ronin.core.loop import StalledError, run_turn
from ronin.core.protocols import FinalMessage, ModelChunk, TextChunk
from ronin.core.types import (
    AgentState,
    ApprovalDecision,
    ApprovalRequest,
    Budget,
    DangerLevel,
    Message,
    Role,
    StreamReset,
    Text,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolSpec,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
)

SPECS: dict[str, ToolSpec] = {
    "read_file": ToolSpec(
        name="read_file",
        description="Read a file.",
        danger_level=DangerLevel.READ_ONLY,
    ),
    "list_files": ToolSpec(
        name="list_files",
        description="List a directory.",
        danger_level=DangerLevel.READ_ONLY,
    ),
    "delete_tree": ToolSpec(
        name="delete_tree",
        description="Delete a directory tree.",
        danger_level=DangerLevel.DESTRUCTIVE,
        requires_approval=True,
    ),
}


class ScriptedModel:
    """Replays canned turns. Stands in for a provider; knows nothing about one."""

    def __init__(self, script: Sequence[Sequence[ModelChunk]]) -> None:
        self._script = list(script)
        self._turn = 0

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ModelChunk]:
        if self._turn >= len(self._script):
            raise AssertionError("scripted model exhausted")
        chunks = self._script[self._turn]
        self._turn += 1

        async def _gen() -> AsyncIterator[ModelChunk]:
            for chunk in chunks:
                await asyncio.sleep(0)
                yield chunk

        return _gen()


class DemoTools:
    """Read-only tools that really run, plus one that would be destructive."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def specs(self) -> Sequence[ToolSpec]:
        return list(SPECS.values())

    def get(self, name: str) -> ToolSpec | None:
        return SPECS.get(name)

    async def execute(self, use: ToolUse) -> ToolResult:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)  # long enough to overlap visibly
            self.executed.append(use.name)
            if use.name == "list_files":
                return ToolResult(ok=True, content="README.md\npyproject.toml")
            if use.name == "read_file":
                # Deliberately huge, to show the truncation marker.
                body = "\n".join(f"line {n}" for n in range(400))
                return ToolResult(ok=True, content=body, tokens_estimate=400)
            return ToolResult(ok=False, error="unreachable in this demo")
        finally:
            self.in_flight -= 1


class DemoPolicy:
    """Approves nothing dangerous, and never runs out of budget."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def approve(self, spec: ToolSpec, use: ToolUse, *, rendered: str) -> ApprovalDecision:
        self.asked.append(rendered)
        return ApprovalDecision(approved=False, reason="the demo policy declines destructive tools")

    def check_budget(self, budget: Budget) -> str | None:
        return None

    def cancelled(self) -> bool:
        return False


def _assistant(text: str, *calls: ToolUse) -> FinalMessage:
    blocks: tuple[Text | ToolUse, ...] = ((Text(text),) if text else ()) + calls
    return FinalMessage(
        message=Message(role=Role.ASSISTANT, content_blocks=blocks),
        input_tokens=120,
        output_tokens=40,
        cost_usd=0.0004,
    )


# Scenario A: parallel read-only calls, a denied destructive call, a truncated
# result, one stall nudge — then the model changes course and finishes cleanly.
SCRIPT_A: list[list[ModelChunk]] = [
    [
        TextChunk("Let me look around. "),
        _assistant(
            "Reading both files.",
            ToolUse(id="c1", name="list_files", arguments={"path": "."}),
            ToolUse(id="c2", name="read_file", arguments={"path": "README.md"}),
        ),
    ],
    [
        _assistant(
            "Now I'll clean up.",
            ToolUse(id="c3", name="delete_tree", arguments={"path": "/"}),
        )
    ],
    [_assistant("", ToolUse(id="c4", name="list_files", arguments={"path": "."}))],
    # third identical call ⇒ nudge injected
    [_assistant("", ToolUse(id="c5", name="list_files", arguments={"path": "."}))],
    # the model takes the hint and answers
    [TextChunk("Understood — README.md is the entry point."), _assistant("Done.")],
]

# Scenario B: the model ignores the nudge and repeats anyway ⇒ StalledError.
SCRIPT_B: list[list[ModelChunk]] = [
    [_assistant("", ToolUse(id=f"s{n}", name="list_files", arguments={"path": "."}))]
    for n in range(5)
]


def _print_event(event: object) -> TurnEnd | None:
    match event:
        case TurnStart(turn_index=i):
            print(f"\n[turn {i}]")
        case TextDelta(text=text, thinking=thinking):
            print(f"  {'thinking' if thinking else 'text'}: {text!r}")
        case StreamReset(reason=reason):
            print(f"  reset: {reason}  (discard rendered text)")
        case ToolStart(name=name, tool_use_id=tid):
            print(f"  → {name} [{tid}]")
        case ApprovalRequest(rendered=rendered):
            print(f"  ?  approval needed: {rendered}")
        case ToolEnd(name=name, result=result):
            body = result.content if result.ok else result.error
            head_line = body.splitlines()[0] if body else ""
            print(f"  ← {name}: ok={result.ok} {head_line[:56]!r}")
        case TurnEnd() as end:
            print(f"\n  turn ended: {end.state.value} ({end.stop_reason})")
            return end
    return None


def _summarize(ended: AgentState, tools: DemoTools, policy: DemoPolicy) -> None:
    print(f"  tools executed        : {tools.executed}")
    print(
        f"  max concurrent tools  : {tools.max_in_flight}"
        "   (2 ⇒ the read-only batch ran in parallel)"
    )
    print(f"  approvals asked       : {policy.asked}")
    print(f"  messages in transcript: {len(ended.messages)}")
    print(
        f"  budget spent          : {ended.budget.spent_tokens} tokens, "
        f"${ended.budget.spent_usd:.4f}"
    )
    print(f"  unpaired tool calls   : {ended.pairing_errors or '(none — transcript is sendable)'}")
    nudges = [
        m
        for m in ended.messages
        if m.role is Role.SYSTEM and m.metadata.get("kind") == "stall_nudge"
    ]
    print(f"  stall nudges injected : {len(nudges)}")
    if nudges:
        print(f"    └ {nudges[0].text}")
    truncated = [b for m in ended.messages for b in m.tool_results if "…[truncated:" in b.content]
    if truncated:
        print(f"  truncation marker     : {truncated[0].content.splitlines()[-1]}")


async def _scenario(name: str, script: list[list[ModelChunk]]) -> None:
    print(f"\n{'═' * 62}\n{name}\n{'═' * 62}")
    tools, policy = DemoTools(), DemoPolicy()
    state = AgentState(
        messages=(Message(role=Role.USER, content_blocks=(Text("Tidy this repo."),)),),
        budget=Budget(max_tokens=100_000, max_usd=1.0),
    )
    final: TurnEnd | None = None
    try:
        async for event in run_turn(
            state, ScriptedModel(script), tools, policy, system="You are Ronin."
        ):
            final = _print_event(event) or final
    except StalledError as exc:
        print(f"\n  aborted: {exc}")
        print("\n── state survived the abort " + "─" * 34)
        if exc.agent_state is not None:
            _summarize(exc.agent_state, tools, policy)
        return

    print("\n── what just happened " + "─" * 40)
    if final is not None and final.agent_state is not None:
        _summarize(final.agent_state, tools, policy)


async def main() -> int:
    await _scenario("A · parallel tools, denied approval, truncation, one nudge", SCRIPT_A)
    await _scenario("B · the model ignores the nudge ⇒ StalledError", SCRIPT_B)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
