"""Replay recorded bytes from every provider shape; require identical normalization.

This is the test the whole layer exists to pass. Three providers, three wire
formats, three sets of quirks — and one ``ToolUse``. If this file passes, swapping
Claude for Kimi K2 for a local Qwen really is a config change, because the thing the
loop sees is byte-identical.

Every fixture is replayed under six chunkings (see ``replay.SPLIT_SIZES``),
including one byte at a time. A result that depends on how the network happened to
fragment the response is a bug, and this is where it shows up.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from replay import ReplayTransport, chunk_by, load, load_tokens, splits, token_generator

from ronin.core.types import Text, Thinking, ToolUse
from ronin.providers import (
    AnthropicClient,
    Capabilities,
    Completed,
    FinishReason,
    MLXClient,
    ModelRequest,
    MoonshotClient,
    OpenAICompatClient,
    ShimClient,
    TextDelta,
    ThinkingDelta,
)
from ronin.providers.base import ModelClient
from ronin.providers.types import ModelDelta

REQUEST = ModelRequest(model="m", system="You are Ronin.", messages=())


@dataclass(frozen=True, slots=True)
class Drained:
    """A whole stream, flattened for comparison."""

    text: str
    thinking: str
    deltas: tuple[ModelDelta, ...]
    completed: Completed

    @property
    def uses(self) -> tuple[ToolUse, ...]:
        return self.completed.tool_uses

    @property
    def calls(self) -> tuple[tuple[str, tuple[tuple[str, object], ...]], ...]:
        """Calls as comparable plain data: name plus sorted arguments.

        Ids are excluded on purpose — they are provider-supplied or minted, and two
        providers expressing the same call are not obliged to agree on the id. What
        they *are* obliged to agree on is the name and the arguments.
        """
        return tuple(
            (use.name, tuple(sorted(use.arguments.items()))) for use in self.uses
        )


async def drain(stream: AsyncIterator[ModelDelta]) -> Drained:
    """Consume a stream, asserting the one-terminal-Completed invariant."""
    deltas: list[ModelDelta] = []
    text: list[str] = []
    thinking: list[str] = []
    completed: Completed | None = None
    async for delta in stream:
        deltas.append(delta)
        if isinstance(delta, TextDelta):
            text.append(delta.text)
        elif isinstance(delta, ThinkingDelta):
            thinking.append(delta.text)
        elif isinstance(delta, Completed):
            assert completed is None, "a stream must contain exactly one Completed"
            completed = delta
    assert completed is not None, "a stream must end with a Completed"
    assert isinstance(deltas[-1], Completed), "Completed must be the last delta"
    return Drained(
        text="".join(text),
        thinking="".join(thinking),
        deltas=tuple(deltas),
        completed=completed,
    )


def anthropic(fixture: str, chunk_size: int, **kwargs: object) -> tuple[ModelClient, object]:
    transport = ReplayTransport(chunk_by(load(fixture), chunk_size))
    client = AnthropicClient(model="claude-x", transport=transport, **kwargs)  # type: ignore[arg-type]
    return client, transport


def openai(fixture: str, chunk_size: int) -> tuple[ModelClient, object]:
    transport = ReplayTransport(chunk_by(load(fixture), chunk_size))
    client = OpenAICompatClient(model="gpt-x", transport=transport, base_url="openai")
    return client, transport


def moonshot(fixture: str, chunk_size: int) -> tuple[ModelClient, object]:
    transport = ReplayTransport(chunk_by(load(fixture), chunk_size))
    client = MoonshotClient(model="kimi-k2", transport=transport)
    return client, transport


def local(fixture: str) -> ShimClient:
    inner = MLXClient(
        model="qwen-local",
        generate=token_generator(load_tokens(fixture)),
    )
    return ShimClient(inner)


# --------------------------------------------------------------------------- #
# The headline assertion
# --------------------------------------------------------------------------- #

#: The same logical response — "say a sentence, then read src/main.py" — as each
#: provider actually puts it on the wire.
EQUIVALENT_HAPPY_PATHS = (
    "anthropic",
    "openai",
    "openai-whole-message",
    "moonshot",
    "local-shim",
)


async def _drain_happy(kind: str) -> Drained:
    if kind == "anthropic":
        client, _ = anthropic("anthropic/happy.sse", 0)
    elif kind == "openai":
        client, _ = openai("openai/happy.sse", 0)
    elif kind == "openai-whole-message":
        client, _ = openai("openai/whole_message.sse", 0)
    elif kind == "moonshot":
        client, _ = moonshot("moonshot/happy.sse", 0)
    else:
        client = local("mlx/happy.tokens")
    return await drain(client.stream(REQUEST))


async def test_every_provider_normalizes_the_same_call_identically() -> None:
    results = {kind: await _drain_happy(kind) for kind in EQUIVALENT_HAPPY_PATHS}
    expected = (("read_file", (("path", "src/main.py"),)),)
    for kind, drained in results.items():
        assert drained.calls == expected, f"{kind} normalized differently"
        assert drained.completed.finish is FinishReason.TOOL_USE, kind
        assert "read" in drained.text.lower(), f"{kind} lost the visible text"


async def test_no_provider_leaks_a_tool_call_into_the_visible_text() -> None:
    """The user must never see the mechanism, on any provider."""
    for kind in EQUIVALENT_HAPPY_PATHS:
        drained = await _drain_happy(kind)
        assert "ronin:tool_call" not in drained.text, kind
        assert "read_file" not in drained.text, kind
        assert '"path"' not in drained.text, kind


# --------------------------------------------------------------------------- #
# Chunking independence
# --------------------------------------------------------------------------- #

SSE_FIXTURES = (
    "anthropic/happy.sse",
    "anthropic/parallel_tools.sse",
    "anthropic/thinking.sse",
    "anthropic/malformed_trailing_comma.sse",
    "anthropic/malformed_truncated.sse",
    "anthropic/malformed_missing_id.sse",
    "openai/happy.sse",
    "openai/whole_message.sse",
    "openai/reasoning.sse",
    "openai/malformed_fenced_args.sse",
    "openai/malformed_colliding_ids.sse",
    "openai/malformed_double_encoded.sse",
    "moonshot/happy.sse",
    "moonshot/malformed_missing_index.sse",
    "moonshot/malformed_name_ids.sse",
    "moonshot/malformed_python_literals.sse",
)


def _client_for(fixture: str, chunk_size: int) -> ModelClient:
    family = fixture.split("/", 1)[0]
    if family == "anthropic":
        return anthropic(fixture, chunk_size)[0]
    if family == "moonshot":
        return moonshot(fixture, chunk_size)[0]
    return openai(fixture, chunk_size)[0]


@pytest.mark.parametrize("fixture", SSE_FIXTURES)
async def test_normalization_is_independent_of_chunk_boundaries(fixture: str) -> None:
    baseline: Drained | None = None
    for size, chunks in splits(load(fixture)):
        client = _client_for(fixture, size)
        del chunks  # the client re-chunks from the same bytes
        drained = await drain(client.stream(REQUEST))
        if baseline is None:
            baseline = drained
            continue
        assert drained.text == baseline.text, f"{fixture} text changed at size={size}"
        assert drained.thinking == baseline.thinking, f"{fixture} thinking changed ({size})"
        assert drained.uses == baseline.uses, f"{fixture} tool calls changed at size={size}"
        assert drained.completed.finish is baseline.completed.finish, f"{fixture} ({size})"
        assert drained.completed.usage == baseline.completed.usage, f"{fixture} ({size})"


@pytest.mark.parametrize("fixture", SSE_FIXTURES)
async def test_every_fixture_ends_with_exactly_one_completed(fixture: str) -> None:
    drained = await drain(_client_for(fixture, 7).stream(REQUEST))
    assert sum(isinstance(d, Completed) for d in drained.deltas) == 1


# --------------------------------------------------------------------------- #
# Anthropic specifics
# --------------------------------------------------------------------------- #


async def test_anthropic_streams_thinking_separately_and_keeps_the_signature() -> None:
    client, _ = anthropic("anthropic/thinking.sse", 5)
    drained = await drain(client.stream(REQUEST))
    assert drained.thinking == "The file is small, so read it whole."
    assert drained.text == "Reading it."
    blocks = drained.completed.message.content_blocks
    thinking = next(b for b in blocks if isinstance(b, Thinking))
    assert thinking.signature == "sig-abc"
    # Thinking must not end up in the answer text.
    assert "small" not in drained.completed.message.text


async def test_anthropic_reports_cache_tokens_separately_from_input() -> None:
    client, _ = anthropic("anthropic/thinking.sse", 0)
    usage = (await drain(client.stream(REQUEST))).completed.usage
    assert usage.input_tokens == 120
    assert usage.cache_read_tokens == 100
    assert usage.cache_write_tokens == 20
    assert usage.cacheable_input == 120


async def test_anthropic_does_not_double_count_the_first_output_token() -> None:
    """``message_delta`` reports a running total; adding it would over-bill."""
    client, _ = anthropic("anthropic/happy.sse", 0)
    usage = (await drain(client.stream(REQUEST))).completed.usage
    # message_start said output_tokens=1, message_delta said 26. The answer is 26.
    assert usage.output_tokens == 26


async def test_anthropic_maps_content_block_index_to_tool_ordinal() -> None:
    """Tool calls at content indices 1 and 2 are calls 0 and 1, not 1 and 2."""
    client, _ = anthropic("anthropic/parallel_tools.sse", 11)
    drained = await drain(client.stream(REQUEST))
    assert drained.calls == (
        ("read_file", (("path", "a.py"),)),
        ("read_file", (("path", "b.py"),)),
    )
    assert [use.id for use in drained.uses] == ["toolu_01", "toolu_02"]


async def test_anthropic_places_the_cache_marker_on_the_last_tool() -> None:
    transport = ReplayTransport(chunk_by(load("anthropic/happy.sse"), 0))
    client = AnthropicClient(model="claude-x", transport=transport)
    from ronin.core.types import ToolSpec

    req = ModelRequest(
        model="claude-x",
        system="stable",
        tools=(
            ToolSpec(name="a", description="first"),
            ToolSpec(name="b", description="last"),
        ),
        cache_marker=1,
    )
    await drain(client.stream(req))
    body = transport.last_body
    tools = body["tools"]
    assert "cache_control" not in tools[0]
    assert tools[1]["cache_control"] == {"type": "ephemeral"}
    # With tools present the system block must NOT also be marked: two markers
    # would cost two cache writes for one prefix.
    assert all("cache_control" not in block for block in body["system"])


async def test_anthropic_marks_the_system_block_when_there_are_no_tools() -> None:
    transport = ReplayTransport(chunk_by(load("anthropic/happy.sse"), 0))
    client = AnthropicClient(model="claude-x", transport=transport)
    await drain(client.stream(ModelRequest(model="claude-x", system="stable", cache_marker=0)))
    assert transport.last_body["system"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_anthropic_omits_the_cache_marker_when_the_model_cannot_cache() -> None:
    transport = ReplayTransport(chunk_by(load("anthropic/happy.sse"), 0))
    client = AnthropicClient(
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
    await drain(client.stream(ModelRequest(model="claude-x", system="stable", cache_marker=0)))
    assert all("cache_control" not in block for block in transport.last_body["system"])


# --------------------------------------------------------------------------- #
# OpenAI-compatible specifics
# --------------------------------------------------------------------------- #


async def test_openai_subtracts_cached_tokens_from_the_input_count() -> None:
    """`prompt_tokens` includes cached reads here; Anthropic reports them apart."""
    client, _ = openai("openai/reasoning.sse", 0)
    usage = (await drain(client.stream(REQUEST))).completed.usage
    assert usage.cache_read_tokens == 400
    assert usage.input_tokens == 100
    assert usage.total_tokens == 131


async def test_openai_surfaces_reasoning_as_thinking_not_as_the_answer() -> None:
    client, _ = openai("openai/reasoning.sse", 3)
    drained = await drain(client.stream(REQUEST))
    assert drained.thinking == "Small file, read it whole."
    assert drained.text == "Reading it."
    assert drained.completed.finish is FinishReason.STOP


async def test_openai_asks_for_usage_in_the_stream() -> None:
    client, transport = openai("openai/happy.sse", 0)
    await drain(client.stream(REQUEST))
    assert isinstance(transport, ReplayTransport)
    assert transport.last_body["stream_options"] == {"include_usage": True}


async def test_openai_resolves_the_known_base_url_alias() -> None:
    client, transport = openai("openai/happy.sse", 0)
    await drain(client.stream(REQUEST))
    assert isinstance(transport, ReplayTransport)
    assert transport.calls[-1]["url"] == "https://api.openai.com/v1/chat/completions"


# --------------------------------------------------------------------------- #
# The three malformed payloads, per provider
# --------------------------------------------------------------------------- #


async def test_trailing_comma_in_streamed_args_is_repaired_and_recorded() -> None:
    client, _ = anthropic("anthropic/malformed_trailing_comma.sse", 4)
    drained = await drain(client.stream(REQUEST))
    assert drained.calls == (("write_file", (("content", "x"), ("path", "a.py"))),)
    assert any("removed_trailing_comma" in note for note in drained.completed.notes)


async def test_a_truncated_stream_recovers_the_arguments_that_arrived() -> None:
    """A cut connection must not lose the call the model actually made."""
    client, _ = anthropic("anthropic/malformed_truncated.sse", 6)
    drained = await drain(client.stream(REQUEST))
    assert len(drained.uses) == 1
    use = drained.uses[0]
    assert use.name == "write_file"
    assert use.arguments["path"] == "a.py"
    assert any("truncated" in note for note in drained.completed.notes)


async def test_a_missing_id_is_minted_deterministically() -> None:
    first, _ = anthropic("anthropic/malformed_missing_id.sse", 0)
    second, _ = anthropic("anthropic/malformed_missing_id.sse", 2)
    one = await drain(first.stream(REQUEST))
    two = await drain(second.stream(REQUEST))
    assert one.uses[0].id == two.uses[0].id, "a minted id must not depend on chunking"
    assert one.uses[0].id.startswith("ron_")
    assert any("minted id" in note for note in one.completed.notes)


async def test_fenced_arguments_are_unwrapped() -> None:
    client, _ = openai("openai/malformed_fenced_args.sse", 5)
    drained = await drain(client.stream(REQUEST))
    assert drained.calls == (("read_file", (("path", "src/main.py"),)),)
    assert any("stripped_fence" in note for note in drained.completed.notes)


async def test_colliding_ids_are_disambiguated_so_pairing_stays_unambiguous() -> None:
    client, _ = openai("openai/malformed_colliding_ids.sse", 0)
    drained = await drain(client.stream(REQUEST))
    ids = [use.id for use in drained.uses]
    assert len(ids) == 2
    assert len(set(ids)) == 2, "two calls sharing one id would break tool_result pairing"
    assert ids[0] == "call_0"
    assert any("collided" in note for note in drained.completed.notes)


async def test_double_encoded_arguments_are_unwrapped_once() -> None:
    client, _ = openai("openai/malformed_double_encoded.sse", 0)
    drained = await drain(client.stream(REQUEST))
    assert drained.calls == (("read_file", (("path", "src/main.py"),)),)
    assert any("double_encoded" in note for note in drained.completed.notes)


async def test_moonshot_discards_a_name_derived_id_and_mints_a_real_one() -> None:
    client, _ = moonshot("moonshot/happy.sse", 0)
    drained = await drain(client.stream(REQUEST))
    use = drained.uses[0]
    assert use.id != "functions.read_file:0"
    assert use.id.startswith("ron_")
    assert any("derived from the tool name" in note for note in drained.completed.notes)


async def test_moonshot_trusts_the_presence_of_calls_over_a_stop_label() -> None:
    """The fixture says ``finish_reason: "stop"`` on a turn that made a call."""
    client, _ = moonshot("moonshot/happy.sse", 0)
    drained = await drain(client.stream(REQUEST))
    assert drained.uses
    assert drained.completed.finish is FinishReason.TOOL_USE


async def test_moonshot_fragments_without_an_index_continue_the_same_call() -> None:
    client, _ = moonshot("moonshot/malformed_missing_index.sse", 0)
    drained = await drain(client.stream(REQUEST))
    assert drained.calls == (("read_file", (("path", "src/main.py"),)),)


async def test_moonshot_two_calls_sharing_a_name_derived_id_stay_distinct() -> None:
    client, _ = moonshot("moonshot/malformed_name_ids.sse", 0)
    drained = await drain(client.stream(REQUEST))
    ids = [use.id for use in drained.uses]
    assert len(ids) == 2
    assert len(set(ids)) == 2
    assert drained.calls == (
        ("read_file", (("path", "a.py"),)),
        ("read_file", (("path", "b.py"),)),
    )


async def test_python_literals_in_arguments_are_converted() -> None:
    client, _ = moonshot("moonshot/malformed_python_literals.sse", 0)
    drained = await drain(client.stream(REQUEST))
    assert drained.calls == (("search", (("pattern", "def "), ("regex", True))),)


# --------------------------------------------------------------------------- #
# Local model, through the shim
# --------------------------------------------------------------------------- #


async def test_local_model_tag_split_across_tokens_never_leaks() -> None:
    drained = await drain(local("mlx/happy.tokens").stream(REQUEST))
    assert drained.text == "I'll read that file. "
    assert drained.calls == (("read_file", (("path", "src/main.py"),)),)


async def test_local_model_two_calls_in_one_block_both_survive() -> None:
    drained = await drain(local("mlx/two_calls_one_block.tokens").stream(REQUEST))
    assert drained.calls == (
        ("read_file", (("path", "a.py"),)),
        ("read_file", (("path", "b.py"),)),
    )


async def test_prose_containing_an_angle_bracket_is_released_not_swallowed() -> None:
    drained = await drain(local("mlx/angle_bracket_prose.tokens").stream(REQUEST))
    assert drained.text == "if a < b and c <ronin doubt, compare."
    assert drained.calls == ()
    assert drained.completed.finish is FinishReason.STOP


async def test_local_malformed_call_is_repaired_by_jsonargs_not_by_a_retry() -> None:
    drained = await drain(local("mlx/malformed_trailing_comma.tokens").stream(REQUEST))
    assert drained.calls == (("read_file", (("path", "a.py"),)),)


async def test_a_stream_that_ends_inside_a_call_surfaces_after_repairs() -> None:
    """The payload must not leak as prose, and the turn must not look successful."""
    inner = MLXClient(
        model="q",
        generate=token_generator(load_tokens("mlx/malformed_unterminated.tokens")),
    )
    drained = await drain(ShimClient(inner, max_repairs=1).stream(REQUEST))
    assert drained.completed.finish is FinishReason.ERROR
    assert drained.calls == ()
    assert '{"name"' not in drained.completed.message.text
    assert any("never emitted" in note for note in drained.completed.notes)
    assert any("gave up after" in note for note in drained.completed.notes)


async def test_a_block_with_no_name_is_reported_not_executed() -> None:
    inner = MLXClient(
        model="q",
        generate=token_generator(load_tokens("mlx/malformed_no_name.tokens")),
    )
    drained = await drain(ShimClient(inner, max_repairs=0).stream(REQUEST))
    assert drained.calls == ()
    assert drained.completed.finish is FinishReason.ERROR
    assert any('no "name" key' in note for note in drained.completed.notes)


async def test_the_shim_never_promises_parallel_dispatch() -> None:
    caps = local("mlx/happy.tokens").capabilities()
    assert caps.native_tools is False
    assert caps.parallel_tools is False


async def test_local_usage_is_reported_as_unknown_not_as_zero_cost() -> None:
    drained = await drain(local("mlx/happy.tokens").stream(REQUEST))
    assert drained.completed.usage.total_tokens == 0
    assert any("not reported" in note for note in drained.completed.notes)


async def test_visible_text_is_reassembled_verbatim_across_token_boundaries() -> None:
    """Whatever was withheld must be released, in order, exactly once."""
    drained = await drain(local("mlx/happy.tokens").stream(REQUEST))
    streamed = "".join(d.text for d in drained.deltas if isinstance(d, TextDelta))
    assert streamed == drained.text
    text_blocks = [b for b in drained.completed.message.content_blocks if isinstance(b, Text)]
    assert len(text_blocks) == 1
    assert text_blocks[0].text == streamed
