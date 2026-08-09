"""The streaming parser and the repair loop.

The parser tests are mostly about one guarantee: **no byte that could still be part
of a tag is ever emitted as visible text, and every byte that turns out not to be
part of a tag is eventually emitted exactly once.** Those two together are what a
regex over the finished string cannot give you, and the reason this is a state
machine.

The repair-loop tests use a scripted fake model rather than a fixture, because what
is being tested is a *conversation*: what we send back after a bad call, and what
happens on the third strike.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from ronin.core.types import DangerLevel, Message, Role, Text, ToolSpec
from ronin.providers.shim import (
    CLOSE_TAG,
    FORMAT_RULES,
    OPEN_TAG,
    ShimClient,
    ShimStreamParser,
    build_shim_system,
    render_tool_specs,
    repair_message,
)
from ronin.providers.types import (
    Capabilities,
    Completed,
    FinishReason,
    ModelDelta,
    ModelRequest,
    StreamReset,
    TextDelta,
    ThinkingDelta,
    Usage,
)

SPECS = (
    ToolSpec(
        name="read_file",
        description="Read a file from disk.",
        json_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    ),
    ToolSpec(
        name="delete_file",
        description="Delete a file.",
        danger_level=DangerLevel.DESTRUCTIVE,
        requires_approval=True,
    ),
)

REQUEST = ModelRequest(model="local", system="You are Ronin.", tools=SPECS)


def feed_all(chunks: Sequence[str]) -> tuple[str, list[str], list[str]]:
    """Feed chunks through a parser and return (visible, call names, failures)."""
    parser = ShimStreamParser()
    visible: list[str] = []
    names: list[str] = []
    errors: list[str] = []
    for chunk in chunks:
        emit = parser.feed(chunk)
        visible.append(emit.visible)
        names.extend(call.name for call in emit.calls)
        errors.extend(failure.error for failure in emit.failures)
    tail = parser.finish()
    visible.append(tail.visible)
    names.extend(call.name for call in tail.calls)
    errors.extend(failure.error for failure in tail.failures)
    return "".join(visible), names, errors


def split_every(text: str, size: int) -> tuple[str, ...]:
    return tuple(text[i : i + size] for i in range(0, len(text), size))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def test_specs_render_as_closed_xml_with_the_schema_inline() -> None:
    block = render_tool_specs(SPECS)
    assert block.startswith("<ronin:tools>")
    assert "</ronin:tools>" in block
    assert "<name>read_file</name>" in block
    assert '"properties"' in block
    assert block.count("<ronin:tool>") == 2


def test_no_specs_renders_nothing() -> None:
    assert render_tool_specs(()) == ""
    assert build_shim_system("base", ()) == "base"


def test_the_tool_block_goes_after_the_base_prompt() -> None:
    system = build_shim_system("BASE", SPECS)
    assert system.index("BASE") < system.index("<ronin:tools>")


def test_an_empty_base_prompt_yields_just_the_block() -> None:
    assert build_shim_system("", SPECS).startswith("<ronin:tools>")


def test_the_repair_message_quotes_the_payload_and_restates_one_format() -> None:
    message = repair_message('{"name": "x",}', "trailing comma")
    body = message.text
    assert message.role is Role.USER
    assert '{"name": "x",}' in body
    assert "trailing comma" in body
    # The format must be the *same* constant the system prompt used.
    assert FORMAT_RULES in body
    assert FORMAT_RULES in render_tool_specs(SPECS)


# --------------------------------------------------------------------------- #
# The parser: nothing leaks
# --------------------------------------------------------------------------- #


WHOLE = f'Reading it. {OPEN_TAG}{{"name": "read_file", "arguments": {{"path": "a.py"}}}}{CLOSE_TAG}'


@pytest.mark.parametrize("size", [1, 2, 3, 5, 8, 13, 17, 40, 1000])
def test_a_call_is_found_at_every_chunk_size(size: int) -> None:
    visible, names, errors = feed_all(split_every(WHOLE, size))
    assert names == ["read_file"], size
    assert errors == [], size
    assert visible == "Reading it. ", size


@pytest.mark.parametrize("size", [1, 2, 3, 5, 8, 13, 17])
def test_no_partial_tag_ever_reaches_the_visible_text(size: int) -> None:
    parser = ShimStreamParser()
    for chunk in split_every(WHOLE, size):
        emit = parser.feed(chunk)
        assert "<" not in emit.visible or "a < b" in emit.visible, (size, emit.visible)
        assert "ronin" not in emit.visible, (size, emit.visible)


def test_a_tag_split_at_every_single_position_still_parses() -> None:
    for cut in range(1, len(WHOLE)):
        _, names, errors = feed_all((WHOLE[:cut], WHOLE[cut:]))
        assert names == ["read_file"], cut
        assert errors == [], cut


def test_prose_with_a_less_than_sign_is_released() -> None:
    visible, names, _ = feed_all(("if a < b", " then c"))
    assert visible == "if a < b then c"
    assert names == []


def test_a_near_miss_prefix_is_released_once_it_cannot_be_a_tag() -> None:
    visible, names, _ = feed_all(("<ronin:to", "ol_other>hello"))
    assert visible == "<ronin:tool_other>hello"
    assert names == []


def test_a_held_prefix_at_end_of_stream_is_released_as_prose() -> None:
    visible, names, errors = feed_all(("text <ronin:to",))
    assert visible == "text <ronin:to"
    assert names == []
    assert errors == []


def test_text_after_a_call_is_visible() -> None:
    visible, names, _ = feed_all((f'a {OPEN_TAG}{{"name":"read_file"}}{CLOSE_TAG} b',))
    assert visible == "a  b"
    assert names == ["read_file"]


def test_two_separate_blocks_are_two_calls() -> None:
    stream = (
        f'{OPEN_TAG}{{"name":"read_file","arguments":{{"path":"a"}}}}{CLOSE_TAG}'
        f' and {OPEN_TAG}{{"name":"read_file","arguments":{{"path":"b"}}}}{CLOSE_TAG}'
    )
    visible, names, _ = feed_all(split_every(stream, 6))
    assert names == ["read_file", "read_file"]
    assert visible == " and "


def test_two_objects_in_one_block_are_two_calls() -> None:
    body = (
        '{"name":"read_file","arguments":{"path":"a"}}'
        '{"name":"read_file","arguments":{"path":"b"}}'
    )
    _, names, _ = feed_all((f"{OPEN_TAG}{body}{CLOSE_TAG}",))
    assert names == ["read_file", "read_file"]


def test_an_unterminated_block_is_a_failure_and_never_prose() -> None:
    visible, names, errors = feed_all((f'{OPEN_TAG}{{"name": "read_', "file"))
    assert visible == ""
    assert names == []
    assert len(errors) == 1
    assert "never emitted" in errors[0]


def test_an_empty_block_is_reported() -> None:
    _, names, errors = feed_all((f"{OPEN_TAG}{CLOSE_TAG}",))
    assert names == []
    assert "empty" in errors[0]


def test_a_block_with_no_name_is_reported() -> None:
    _, names, errors = feed_all((f'{OPEN_TAG}{{"arguments": {{"a": 1}}}}{CLOSE_TAG}',))
    assert names == []
    assert 'no "name" key' in errors[0]


def test_alternative_key_spellings_are_accepted() -> None:
    for body in (
        '{"tool": "read_file", "arguments": {"path": "a"}}',
        '{"name": "read_file", "parameters": {"path": "a"}}',
        '{"tool_name": "read_file", "args": {"path": "a"}}',
    ):
        _, names, errors = feed_all((f"{OPEN_TAG}{body}{CLOSE_TAG}",))
        assert names == ["read_file"], body
        assert errors == [], body


def test_feeding_an_empty_chunk_is_a_no_op() -> None:
    parser = ShimStreamParser()
    emit = parser.feed("")
    assert emit.visible == ""
    assert emit.calls == ()


def test_finish_is_idempotent_after_an_unterminated_block() -> None:
    parser = ShimStreamParser()
    parser.feed(f'{OPEN_TAG}{{"na')
    first = parser.finish()
    second = parser.finish()
    assert first.failures
    assert second.failures == ()
    assert second.visible == ""


# --------------------------------------------------------------------------- #
# A scripted model for the repair loop
# --------------------------------------------------------------------------- #

LOCAL_CAPS = Capabilities(
    native_tools=False,
    parallel_tools=False,
    prompt_cache=False,
    thinking=False,
    max_context=8192,
    vision=False,
)


class ScriptedModel:
    """Replays one scripted response per call, recording what it was asked."""

    def __init__(self, responses: Sequence[Sequence[ModelDelta]]) -> None:
        self._responses = tuple(responses)
        self.requests: list[ModelRequest] = []

    def capabilities(self) -> Capabilities:
        return LOCAL_CAPS

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        index = len(self.requests)
        self.requests.append(req)
        if index >= len(self._responses):
            raise AssertionError(f"model called {index + 1}x, {len(self._responses)} scripted")
        for delta in self._responses[index]:
            yield delta


def text_then_done(text: str, *, usage: Usage | None = None) -> tuple[ModelDelta, ...]:
    """A response that streams ``text`` and finishes with no native tool calls."""
    return (
        TextDelta(text),
        Completed(
            message=Message(role=Role.ASSISTANT, content_blocks=(Text(text),)),
            finish=FinishReason.STOP,
            usage=usage or Usage(input_tokens=10, output_tokens=5),
        ),
    )


async def drain(stream: AsyncIterator[ModelDelta]) -> tuple[list[ModelDelta], Completed]:
    deltas = [delta async for delta in stream]
    completed = deltas[-1]
    assert isinstance(completed, Completed)
    return deltas, completed


# --------------------------------------------------------------------------- #
# The client: specs move to the prompt, tools leave the request
# --------------------------------------------------------------------------- #


async def test_the_shim_moves_specs_into_the_system_prompt_and_clears_tools() -> None:
    model = ScriptedModel([text_then_done("done")])
    await drain(ShimClient(model).stream(REQUEST))
    sent = model.requests[0]
    assert sent.tools == (), "a shimmed model must not be sent a tools field"
    assert "<ronin:tools>" in sent.system
    assert "read_file" in sent.system
    assert sent.system.startswith("You are Ronin.")


async def test_the_shim_reports_native_tools_false_even_if_the_inner_lies() -> None:
    class Liar(ScriptedModel):
        def capabilities(self) -> Capabilities:
            return Capabilities(
                native_tools=True,
                parallel_tools=True,
                prompt_cache=True,
                thinking=True,
                max_context=1000,
                vision=False,
            )

    caps = ShimClient(Liar([])).capabilities()
    assert caps.native_tools is False
    assert caps.parallel_tools is False
    # Everything else is passed through untouched.
    assert caps.prompt_cache is True
    assert caps.thinking is True
    assert caps.max_context == 1000


async def test_a_negative_repair_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be >= 0"):
        ShimClient(ScriptedModel([]), max_repairs=-1)


async def test_thinking_is_passed_through_and_never_scanned_for_calls() -> None:
    """A model that *thinks* about a call has not made one."""
    model = ScriptedModel(
        [
            (
                ThinkingDelta(f'maybe {OPEN_TAG}{{"name":"read_file"}}{CLOSE_TAG}'),
                *text_then_done("I won't call it."),
            )
        ]
    )
    deltas, completed = await drain(ShimClient(model).stream(REQUEST))
    assert any(isinstance(d, ThinkingDelta) for d in deltas)
    assert completed.tool_uses == ()


async def test_a_native_call_from_a_supposedly_weak_model_is_still_honoured() -> None:
    from ronin.core.types import ToolUse

    model = ScriptedModel(
        [
            (
                Completed(
                    message=Message(
                        role=Role.ASSISTANT,
                        content_blocks=(
                            ToolUse(id="c1", name="read_file", arguments={"path": "a"}),
                        ),
                    ),
                    finish=FinishReason.TOOL_USE,
                ),
            )
        ]
    )
    _, completed = await drain(ShimClient(model).stream(REQUEST))
    assert [use.name for use in completed.tool_uses] == ["read_file"]


async def test_an_inner_reset_clears_the_parser_and_is_forwarded() -> None:
    """A retry re-streams from the start; held text from the first attempt must go."""
    model = ScriptedModel(
        [
            (
                TextDelta("partial <ronin:to"),
                StreamReset("rate limited, retrying"),
                *text_then_done("full answer"),
            )
        ]
    )
    deltas, completed = await drain(ShimClient(model).stream(REQUEST))
    assert any(isinstance(d, StreamReset) for d in deltas)
    assert completed.message.text == "full answer"


# --------------------------------------------------------------------------- #
# The repair loop
# --------------------------------------------------------------------------- #

BAD = f'{OPEN_TAG}{{"name": "read_file", "arguments": "not an object"}}{CLOSE_TAG}'
UNPARSEABLE = f"{OPEN_TAG}}}{{ not json at all {CLOSE_TAG}"
GOOD = f'{OPEN_TAG}{{"name": "read_file", "arguments": {{"path": "a.py"}}}}{CLOSE_TAG}'


async def test_a_bad_call_is_repaired_on_the_second_attempt() -> None:
    model = ScriptedModel([text_then_done(UNPARSEABLE), text_then_done(GOOD)])
    deltas, completed = await drain(ShimClient(model).stream(REQUEST))
    assert len(model.requests) == 2
    assert [use.name for use in completed.tool_uses] == ["read_file"]
    assert completed.finish is FinishReason.TOOL_USE
    assert any("repaired the tool call after 1" in note for note in completed.notes)
    # The consumer is told to discard what it showed from the failed attempt.
    assert any(isinstance(d, StreamReset) for d in deltas)


async def test_the_repair_turn_replays_the_model_words_then_the_correction() -> None:
    model = ScriptedModel([text_then_done(UNPARSEABLE), text_then_done(GOOD)])
    await drain(ShimClient(model).stream(REQUEST))
    retry = model.requests[1]
    assert len(retry.messages) == 2
    assert retry.messages[0].role is Role.ASSISTANT
    assert retry.messages[1].role is Role.USER
    assert "did not parse" in retry.messages[1].text
    assert FORMAT_RULES in retry.messages[1].text


async def test_the_stable_prefix_is_byte_identical_across_a_repair() -> None:
    """A repair must still be a cache hit, or a bad call costs the whole prefix."""
    model = ScriptedModel([text_then_done(UNPARSEABLE), text_then_done(GOOD)])
    await drain(ShimClient(model).stream(REQUEST))
    first, second = model.requests
    assert first.system == second.system
    assert first.prefix == second.prefix
    assert first.tools == second.tools


async def test_repairs_stop_at_two_and_then_surface() -> None:
    model = ScriptedModel([text_then_done(UNPARSEABLE)] * 3)
    _, completed = await drain(ShimClient(model).stream(REQUEST))
    assert len(model.requests) == 3, "one original attempt plus two repairs"
    assert completed.finish is FinishReason.ERROR
    assert completed.tool_uses == ()
    assert any("gave up after 2 repair attempt" in note for note in completed.notes)


async def test_a_zero_repair_budget_surfaces_immediately() -> None:
    model = ScriptedModel([text_then_done(UNPARSEABLE)])
    _, completed = await drain(ShimClient(model, max_repairs=0).stream(REQUEST))
    assert len(model.requests) == 1
    assert completed.finish is FinishReason.ERROR


async def test_the_failed_payload_is_never_shown_as_the_answer() -> None:
    model = ScriptedModel([text_then_done(UNPARSEABLE)])
    _, completed = await drain(ShimClient(model, max_repairs=0).stream(REQUEST))
    assert OPEN_TAG not in completed.message.text
    assert "not json at all" not in completed.message.text


async def test_arguments_that_are_a_string_are_a_repairable_failure() -> None:
    """``"arguments": "not an object"`` is the shape the format rules forbid."""
    model = ScriptedModel([text_then_done(BAD), text_then_done(GOOD)])
    _, completed = await drain(ShimClient(model).stream(REQUEST))
    assert [use.name for use in completed.tool_uses] == ["read_file"]


async def test_usage_from_the_final_attempt_is_what_is_reported() -> None:
    model = ScriptedModel(
        [
            text_then_done(UNPARSEABLE, usage=Usage(input_tokens=100, output_tokens=10)),
            text_then_done(GOOD, usage=Usage(input_tokens=120, output_tokens=12)),
        ]
    )
    _, completed = await drain(ShimClient(model).stream(REQUEST))
    assert completed.usage.input_tokens == 120
    assert completed.usage.output_tokens == 12


async def test_a_clean_first_attempt_makes_exactly_one_request() -> None:
    model = ScriptedModel([text_then_done(GOOD)])
    _, completed = await drain(ShimClient(model).stream(REQUEST))
    assert len(model.requests) == 1
    assert not any("repaired" in note for note in completed.notes)


async def test_visible_text_survives_a_call_in_the_middle_of_prose() -> None:
    model = ScriptedModel([text_then_done(f"Before. {GOOD} After.")])
    deltas, completed = await drain(ShimClient(model).stream(REQUEST))
    streamed = "".join(d.text for d in deltas if isinstance(d, TextDelta))
    assert streamed == "Before.  After."
    assert completed.message.text == "Before.  After."
    assert [use.name for use in completed.tool_uses] == ["read_file"]
