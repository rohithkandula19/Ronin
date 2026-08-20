"""What the shim hands upward: preserved ids, surviving calls, a reported failure.

Three defects in one seam, all of the same kind — information the shim *had* and
dropped on the way out:

* a provider-issued ``tool_call_id`` re-minted as a synthetic one, so the next
  turn's ``tool_result`` would carry an id the provider never saw
* calls that parsed thrown away because a sibling call did not
* ``FinishReason.ERROR`` and ``notes`` that no consumer read, so an exhausted
  repair budget reached the user as ordinary prose

The last one needed a field on the loop seam (`FinalMessage.error`); the first two
were losses inside this package.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from test_shim import ScriptedModel, drain, text_then_done

from ronin.core import protocols as core
from ronin.core.types import Message, Role, Text, ToolSpec, ToolUse
from ronin.providers.bridge import LoopClient
from ronin.providers.shim import (
    CLOSE_TAG,
    OPEN_TAG,
    ShimClient,
    ShimStreamParser,
)
from ronin.providers.types import (
    Capabilities,
    Completed,
    FinishReason,
    ModelDelta,
    ModelRequest,
    Usage,
)

SPEC = ToolSpec(
    name="read_file",
    description="Read a file.",
    json_schema={"type": "object", "properties": {"path": {"type": "string"}}},
)

LOCAL_CAPS = Capabilities(
    native_tools=False,
    parallel_tools=False,
    prompt_cache=False,
    thinking=False,
    max_context=8192,
    vision=False,
)


def request() -> ModelRequest:
    return ModelRequest(
        model="local/qwen",
        messages=(Message(role=Role.USER, content_blocks=(Text("read a.py"),)),),
        system="be terse",
        tools=(SPEC,),
    )


def good_block(path: str = "a.py") -> str:
    return f'{OPEN_TAG}{{"name":"read_file","arguments":{{"path":"{path}"}}}}{CLOSE_TAG}'


def bad_block() -> str:
    return f'{OPEN_TAG}{{"no_name_here": 1}}{CLOSE_TAG}'


# --------------------------------------------------------------------------- #
# Provider-issued ids survive the shim
# --------------------------------------------------------------------------- #


class NativeInsideShim:
    """A wrapped client that declares no native tools but returns a native call.

    Not contrived: a proxy or a router in front of a local runtime can advertise
    conservatively and still pass a real provider's tool call through.
    """

    def __init__(self, call_id: str) -> None:
        self._call_id = call_id

    def capabilities(self) -> Capabilities:
        return LOCAL_CAPS

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        del req
        use = ToolUse(id=self._call_id, name="read_file", arguments={"path": "a.py"})
        yield Completed(
            message=Message(role=Role.ASSISTANT, content_blocks=(use,)),
            finish=FinishReason.TOOL_USE,
            usage=Usage(input_tokens=5, output_tokens=2),
        )


@pytest.mark.parametrize("call_id", ["call_ABC123", "call_AbC-123_/=+xyz", "toolu_01XyZ"])
async def test_a_native_call_keeps_the_id_the_provider_issued(call_id: str) -> None:
    """`tool_call_id` copied verbatim and never regenerated — the same invariant
    `test_openai_request.py` pins on the write path, now held on this path too.

    Before this, the id was dropped and `ron_…` minted in its place, so the next
    request answered a call the provider had no record of. That is a 400 arriving
    a turn later, looking like a model problem rather than an encoding one.
    """
    _, completed = await drain(ShimClient(NativeInsideShim(call_id)).stream(request()))
    (use,) = completed.message.tool_uses
    assert use.id == call_id, "the shim re-minted a provider id"
    assert not use.id.startswith("ron"), "a synthetic id replaced a real one"


async def test_a_call_parsed_from_text_still_gets_a_synthetic_id() -> None:
    """The other direction: a shimmed model has no id to preserve, so one is
    minted — and it must be deterministic, not random."""
    model = ScriptedModel([text_then_done(good_block())])
    _, completed = await drain(ShimClient(model).stream(request()))
    (use,) = completed.message.tool_uses
    assert use.id, "a call with no id cannot be answered"

    again = ScriptedModel([text_then_done(good_block())])
    _, repeat = await drain(ShimClient(again).stream(request()))
    assert repeat.message.tool_uses[0].id == use.id, "the synthetic id is not stable"


# --------------------------------------------------------------------------- #
# Calls that parsed survive an exhausted repair budget
# --------------------------------------------------------------------------- #


async def test_a_good_call_is_kept_when_a_sibling_call_cannot_be_repaired() -> None:
    """Ask for two files, mis-type one: the other is still real work.

    Discarding it punished the model for its worst call, and the retry replayed
    only the first failure — so the model would re-emit both and lose both again.
    """
    stream = good_block("good.py") + bad_block()
    model = ScriptedModel([text_then_done(stream) for _ in range(3)])
    _, completed = await drain(ShimClient(model).stream(request()))

    assert completed.finish is FinishReason.ERROR, "the failure must still be reported"
    paths = [json.loads(json.dumps(dict(u.arguments)))["path"] for u in completed.message.tool_uses]
    assert paths == ["good.py"], "the call that parsed was thrown away"
    assert any("gave up after" in note for note in completed.notes)


async def test_a_response_with_only_bad_calls_yields_no_calls_at_all() -> None:
    """The relaxation must not invent a call out of a response that had none."""
    model = ScriptedModel([text_then_done(bad_block()) for _ in range(3)])
    _, completed = await drain(ShimClient(model).stream(request()))
    assert completed.finish is FinishReason.ERROR
    assert completed.message.tool_uses == ()


# --------------------------------------------------------------------------- #
# The failure reaches the loop seam
# --------------------------------------------------------------------------- #


class FailingClient:
    """A client that reports a failed turn the way the shim does."""

    def capabilities(self) -> Capabilities:
        return LOCAL_CAPS

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        del req
        yield Completed(
            message=Message(role=Role.ASSISTANT, content_blocks=(Text("I tried"),)),
            finish=FinishReason.ERROR,
            usage=Usage(input_tokens=5, output_tokens=2),
            notes=("gave up after 2 repair attempt(s): no name key",),
        )


async def test_the_bridge_carries_a_failed_finish_across_the_loop_seam() -> None:
    """`finish` and `notes` used to stop at the bridge.

    That made the translation lossy in a direction its docstring did not claim,
    and it is the reason the shim's careful `FinishReason.ERROR` had no effect on
    anything a user could see.
    """
    client = LoopClient(FailingClient(), model="local/qwen")
    chunks = [c async for c in client.stream(system="s", messages=(), tools=())]
    final = chunks[-1]
    assert isinstance(final, core.FinalMessage)
    assert final.error, "a failed turn crossed the seam as if it had succeeded"
    assert "gave up after 2 repair attempt(s)" in final.error
    assert final.notes


async def test_the_bridge_leaves_a_clean_turn_unmarked() -> None:
    model = ScriptedModel([text_then_done("just prose")])
    client = LoopClient(ShimClient(model), model="local/qwen")
    chunks = [c async for c in client.stream(system="s", messages=(), tools=(SPEC,))]
    final = chunks[-1]
    assert isinstance(final, core.FinalMessage)
    assert final.error == ""


# --------------------------------------------------------------------------- #
# The orphan close tag: a decision, recorded
# --------------------------------------------------------------------------- #


def test_an_orphan_close_tag_is_shown_rather_than_swallowed() -> None:
    """Deliberately *not* suppressed, and pinned so the choice is explicit.

    A close tag with no opener is a **complete** tag, not a partial one, so it is
    outside the guarantee this parser makes (never emit a byte that might still
    become part of a tag). The alternative — dropping it — means silently deleting
    text the model actually wrote, and silent deletion is the failure mode this
    module treats as worst. Showing it also makes a mis-formatting model visible
    to the person who has to decide whether to keep using it.
    """
    parser = ShimStreamParser()
    prose = f"hello {CLOSE_TAG} world"
    visible = "".join(parser.feed(ch).visible for ch in prose) + parser.finish().visible
    assert visible == prose
