"""The bridge, and the integration test: the real loop over a real adapter.

``test_the_loop_runs_a_full_turn_over_a_replayed_provider`` is the one that proves
the two halves fit. It uses the actual ``ronin.core.loop.run_turn`` — not a
reimplementation — driven by an actual ``AnthropicClient`` reading actual recorded
bytes off disk. If the vocabularies had drifted, that test would not run at all.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from replay import (
    ReplayTransport,
    ScriptedTransport,
    chunk_by,
    load,
    load_tokens,
    scripted_token_generator,
)

from ronin.core import protocols as core
from ronin.core.loop import StopReason, run_turn
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
    TurnEnd,
    unpaired_tool_uses,
)
from ronin.core.types import (
    TextDelta as CoreTextDelta,
)
from ronin.providers import (
    AnthropicClient,
    Capabilities,
    LoopClient,
    MLXClient,
    MoonshotClient,
    ShimClient,
)

TOOLS = (
    ToolSpec(
        name="read_file",
        description="Read a file from disk.",
        json_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    ),
    ToolSpec(
        name="write_file",
        description="Write a file.",
        danger_level=DangerLevel.MUTATING,
        requires_approval=True,
    ),
)


class Registry:
    """A tool registry that records what ran, and returns values for failures."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    def specs(self) -> Sequence[ToolSpec]:
        return TOOLS

    def get(self, name: str) -> ToolSpec | None:
        return next((spec for spec in TOOLS if spec.name == name), None)

    async def execute(self, use: ToolUse) -> ToolResult:
        self.ran.append(f"{use.name}({use.arguments.get('path')})")
        return ToolResult(ok=True, content=f"contents of {use.arguments.get('path')}")


class AllowAll:
    async def approve(
        self, spec: ToolSpec, use: ToolUse, *, rendered: str
    ) -> ApprovalDecision:
        del spec, use, rendered
        return ApprovalDecision(approved=True, reason="test policy")

    def check_budget(self, budget: Budget) -> str | None:
        del budget
        return None

    def cancelled(self) -> bool:
        return False


def anthropic_bridge(fixture: str, **kwargs: object) -> LoopClient:
    transport = ReplayTransport(chunk_by(load(fixture), 9))
    inner = AnthropicClient(model="claude-x", transport=transport)
    return LoopClient(inner, model="claude-x", **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The integration test
# --------------------------------------------------------------------------- #


async def test_the_loop_runs_a_full_turn_over_a_replayed_provider() -> None:
    """The real ``run_turn``, the real adapter, recorded bytes, no network.

    Two model turns: ask for a tool, then answer with the result in hand. That is
    the shape of every real turn, and it is the shape a single-response fixture
    cannot produce — replaying one tool call forever drives the loop into its stall
    detector instead, which is correct behaviour but proves nothing about finishing.
    """
    tools = Registry()
    state = AgentState(
        messages=(Message(role=Role.USER, content_blocks=(Text("read src/main.py"),)),)
    )
    transport = ScriptedTransport(
        [load("anthropic/happy.sse"), load("anthropic/final_answer.sse")]
    )
    inner = AnthropicClient(model="claude-x", transport=transport)
    bridge = LoopClient(inner, model="claude-x")

    events = [event async for event in run_turn(state, bridge, tools, AllowAll())]

    assert tools.ran == ["read_file(src/main.py)"]
    text = "".join(e.text for e in events if isinstance(e, CoreTextDelta))
    assert "Reading the file." in text
    assert "It defines main() and nothing else." in text

    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert end.stop_reason == StopReason.NO_TOOL_CALLS.value
    # The state the turn hands back must be one a provider would accept.
    assert end.agent_state is not None
    assert unpaired_tool_uses(end.agent_state.messages) == ()
    # Both requests were made, and the second carried the tool result.
    assert len(transport.calls) == 2


async def test_a_shimmed_local_model_drives_the_same_loop() -> None:
    """A local Qwen with no tool-calling head is indistinguishable to the loop."""
    tools = Registry()
    inner = MLXClient(
        model="q",
        generate=scripted_token_generator(
            [load_tokens("mlx/happy.tokens"), ["It defines main()."]]
        ),
    )
    bridge = LoopClient(ShimClient(inner), model="q")
    state = AgentState(
        messages=(Message(role=Role.USER, content_blocks=(Text("read it"),)),)
    )
    events = [event async for event in run_turn(state, bridge, tools, AllowAll())]
    assert tools.ran == ["read_file(src/main.py)"]
    end = events[-1]
    assert isinstance(end, TurnEnd)
    assert end.stop_reason == StopReason.NO_TOOL_CALLS.value
    assert end.agent_state is not None
    assert unpaired_tool_uses(end.agent_state.messages) == ()


# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #


async def drain(stream: AsyncIterator[core.ModelChunk]) -> list[core.ModelChunk]:
    return [chunk async for chunk in stream]


async def test_text_becomes_a_text_chunk_and_thinking_is_tagged() -> None:
    bridge = anthropic_bridge("anthropic/thinking.sse")
    chunks = await drain(bridge.stream(system="s", messages=(), tools=()))
    thinking = [c for c in chunks if isinstance(c, core.TextChunk) and c.thinking]
    plain = [c for c in chunks if isinstance(c, core.TextChunk) and not c.thinking]
    assert "".join(c.text for c in thinking) == "The file is small, so read it whole."
    assert "".join(c.text for c in plain) == "Reading it."


async def test_the_stream_ends_with_exactly_one_final_message() -> None:
    bridge = anthropic_bridge("anthropic/happy.sse")
    chunks = await drain(bridge.stream(system="s", messages=(), tools=TOOLS))
    finals = [c for c in chunks if isinstance(c, core.FinalMessage)]
    assert len(finals) == 1
    assert isinstance(chunks[-1], core.FinalMessage)
    assert [use.name for use in finals[0].message.tool_uses] == ["read_file"]


async def test_a_provider_reset_becomes_a_core_reset_chunk() -> None:
    """Otherwise the text from a discarded attempt stays on the user's screen.

    The shim emits a reset when it re-asks a model to fix a malformed call. The loop
    only clears what it has rendered if that reset reaches it as a ``ResetChunk``.
    """
    inner = MLXClient(
        model="q",
        generate=scripted_token_generator(
            [
                load_tokens("mlx/malformed_unterminated.tokens"),
                load_tokens("mlx/happy.tokens"),
            ]
        ),
    )
    bridge = LoopClient(ShimClient(inner, max_repairs=1), model="q")
    chunks = await drain(bridge.stream(system="s", messages=(), tools=TOOLS))
    resets = [c for c in chunks if isinstance(c, core.ResetChunk)]
    assert len(resets) == 1
    assert "did not parse" in resets[0].reason
    final = chunks[-1]
    assert isinstance(final, core.FinalMessage)
    assert [use.name for use in final.message.tool_uses] == ["read_file"]
    # The text from the abandoned attempt must not be in the final message.
    assert "Let me look." not in final.message.text


async def test_cached_reads_are_folded_back_into_the_loops_input_count() -> None:
    """The loop has one input-token field; a cached read is still an input token."""
    bridge = anthropic_bridge("anthropic/thinking.sse")
    chunks = await drain(bridge.stream(system="s", messages=(), tools=()))
    final = chunks[-1]
    assert isinstance(final, core.FinalMessage)
    assert final.input_tokens == 220  # 120 uncached + 100 cache read


async def test_the_bridge_measures_the_cache_hit_rate_the_loop_never_sees() -> None:
    bridge = anthropic_bridge("anthropic/thinking.sse")
    await drain(bridge.stream(system="s", messages=(), tools=()))
    assert bridge.cache_stats.requests == 1
    assert bridge.cache_stats.read_tokens == 100
    assert bridge.cache_stats.hit_rate > 0.0


# --------------------------------------------------------------------------- #
# Request assembly through the bridge
# --------------------------------------------------------------------------- #


async def test_the_bridge_assembles_through_the_stable_prefix_path() -> None:
    """Not by hand-building a request — the loop path must get the same ordering."""
    bridge = anthropic_bridge("anthropic/happy.sse", repo_map="src/\n  main.py")
    await drain(bridge.stream(system="You are Ronin.", messages=(), tools=TOOLS))
    req = bridge.last_request
    assert req is not None
    assert req.system == "You are Ronin."
    assert req.prefix == "src/\n  main.py"
    assert req.tools == TOOLS
    assert req.cache_marker == 2
    assert req.metadata["prefix_fingerprint"]


async def test_the_prefix_fingerprint_is_stable_across_turns() -> None:
    bridge = anthropic_bridge("anthropic/happy.sse")
    await drain(bridge.stream(system="s", messages=(), tools=TOOLS))
    first = bridge.last_request
    bridge = anthropic_bridge("anthropic/happy.sse")
    await drain(
        bridge.stream(
            system="s",
            messages=(Message(role=Role.USER, content_blocks=(Text("later turn"),)),),
            tools=TOOLS,
        )
    )
    second = bridge.last_request
    assert first is not None and second is not None
    assert first.metadata["prefix_fingerprint"] == second.metadata["prefix_fingerprint"]


async def test_the_bridge_supplies_the_sampling_config_the_loop_has_no_opinion_on() -> None:
    bridge = anthropic_bridge("anthropic/happy.sse", max_tokens=1234, temperature=0.2)
    await drain(bridge.stream(system="s", messages=(), tools=()))
    req = bridge.last_request
    assert req is not None
    assert req.max_tokens == 1234
    assert req.temperature == 0.2


async def test_a_thinking_budget_is_dropped_when_the_model_cannot_think() -> None:
    transport = ReplayTransport(chunk_by(load("moonshot/happy.sse"), 0))
    inner = MoonshotClient(model="kimi-k2", transport=transport)
    bridge = LoopClient(inner, model="kimi-k2", thinking_budget=4000)
    await drain(bridge.stream(system="s", messages=(), tools=()))
    req = bridge.last_request
    assert req is not None
    assert inner.capabilities().thinking is False
    assert req.thinking_budget == 0


async def test_the_bridge_satisfies_the_loops_protocol_at_runtime() -> None:
    bridge = anthropic_bridge("anthropic/happy.sse")
    assert isinstance(bridge, core.ModelClient)


async def test_a_non_caching_model_gets_no_cache_marker_through_the_bridge() -> None:
    transport = ReplayTransport(chunk_by(load("anthropic/happy.sse"), 0))
    inner = AnthropicClient(
        model="claude-x",
        transport=transport,
        capabilities=Capabilities(
            native_tools=True,
            parallel_tools=True,
            prompt_cache=False,
            thinking=False,
            max_context=1000,
            vision=False,
        ),
    )
    bridge = LoopClient(inner, model="claude-x")
    await drain(bridge.stream(system="s", messages=(), tools=TOOLS))
    req = bridge.last_request
    assert req is not None
    assert req.cache_marker == -1
