"""Mid-turn steering: a correction joins the *running* conversation, at one safe seam.

The behaviour under test is the difference between correcting the agent and correcting
the transcript of what it already did wrong. What matters is not that the message
arrives — it is *where* it arrives, because the two neighbouring positions are both
wrong: one iteration earlier splits a ``tool_use`` from its ``tool_result`` and makes the
transcript something a provider rejects; one turn later is the old queueing behaviour
this replaces.

So every test here asserts a *position* in what the model was handed, not just presence.
Offline against the doubles in ``fakes.py``: no network, no provider, nothing patched.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fakes import (
    FakeModel,
    FakePolicy,
    FakeTools,
    call,
    seed,
    spec,
    succeeds,
    text_turn,
    tool_turn,
)

from ronin.core.loop import STEER_KIND, run_turn
from ronin.core.steering import Steering
from ronin.core.types import (
    Event,
    Message,
    Role,
    Text,
    ToolResult,
    ToolResultBlock,
    ToolUse,
    TurnEnd,
    unpaired_tool_uses,
)


async def drive(stream: Any) -> list[Event]:
    return [event async for event in stream]


def texts(message: Message) -> str:
    return "".join(block.text for block in message.content_blocks if isinstance(block, Text))


def kinds(messages: tuple[Message, ...]) -> list[str]:
    """One readable label per message, so a position assertion reads as a sequence."""
    labels: list[str] = []
    for message in messages:
        if any(isinstance(block, ToolResultBlock) for block in message.content_blocks):
            labels.append("tool_result")
        elif any(isinstance(block, ToolUse) for block in message.content_blocks):
            labels.append("assistant_calls")
        elif message.metadata.get("kind") == STEER_KIND:
            labels.append("steer")
        else:
            labels.append(message.role.value)
    return labels


def steering_tool(steering: Steering, *corrections: str) -> Mapping[str, Any]:
    """A tool that types a correction while it runs. The mid-turn keystroke, offline."""

    def handle(arguments: Mapping[str, Any]) -> ToolResult:
        del arguments
        for correction in corrections:
            steering.push(correction)
        return ToolResult(ok=True, content="a.py has 12 lines")

    return {"read": (spec("read"), handle)}


# --------------------------------------------------------------------------- #
# The seam
# --------------------------------------------------------------------------- #


async def test_a_steer_lands_after_the_tool_results_and_before_the_next_model_call() -> None:
    steering = Steering()
    model = FakeModel([tool_turn(call("t1", "read", path="a.py")), text_turn("using pathlib")])
    tools = FakeTools(steering_tool(steering, "actually use pathlib"))

    await drive(run_turn(seed(), model, tools, FakePolicy(), steering=steering.drain))

    # Two model calls: the correction did not start a new turn, it continued this one.
    assert len(model.calls) == 2
    assert kinds(model.calls[1].messages) == [
        "user",
        "assistant_calls",
        "tool_result",
        "steer",
    ]
    assert texts(model.calls[1].messages[-1]) == "actually use pathlib"


async def test_the_steer_is_a_user_message_and_says_how_it_arrived() -> None:
    # USER and not SYSTEM: this is the human talking. Getting the role wrong would
    # misreport who said it to compaction, to a rewind, and to anyone reading back.
    steering = Steering()
    model = FakeModel([tool_turn(call("t1", "read", path="a.py")), text_turn("ok")])
    tools = FakeTools(steering_tool(steering, "stop editing tests"))

    await drive(run_turn(seed(), model, tools, FakePolicy(), steering=steering.drain))

    steer = model.calls[1].messages[-1]
    assert steer.role is Role.USER
    assert steer.metadata == {"kind": "steer"}


async def test_a_steered_transcript_is_still_paired_and_resumable() -> None:
    # The reason the seam is where it is: a user message between a `tool_use` and its
    # `tool_result` is a transcript every provider rejects on resume.
    steering = Steering()
    model = FakeModel([tool_turn(call("t1", "read", path="a.py")), text_turn("done")])
    tools = FakeTools(steering_tool(steering, "use pathlib"))

    events = await drive(run_turn(seed(), model, tools, FakePolicy(), steering=steering.drain))

    # Not vacuous: the steer really is in there, and the pairing survived it.
    assert "steer" in kinds(model.calls[1].messages)
    assert unpaired_tool_uses(model.calls[1].messages) == ()
    ends = [event for event in events if isinstance(event, TurnEnd)]
    assert ends[-1].agent_state is not None
    assert unpaired_tool_uses(ends[-1].agent_state.messages) == ()


async def test_the_running_tool_is_not_cancelled_by_a_steer() -> None:
    # v1 decision, deliberately: the tool finishes and its result is kept. Stopping work
    # is what `esc` is for, and conflating the two makes every correction a gamble about
    # how much progress it costs.
    steering = Steering()
    model = FakeModel([tool_turn(call("t1", "read", path="a.py")), text_turn("ok")])
    tools = FakeTools(steering_tool(steering, "wrong file"))

    await drive(run_turn(seed(), model, tools, FakePolicy(), steering=steering.drain))

    assert tools.executed_names == ("read",)
    assert "steer" in kinds(model.calls[1].messages)
    results = [
        block
        for message in model.calls[1].messages
        for block in message.content_blocks
        if isinstance(block, ToolResultBlock)
    ]
    assert [block.content for block in results] == ["a.py has 12 lines"]


# --------------------------------------------------------------------------- #
# Ordering, batching, and not repeating itself
# --------------------------------------------------------------------------- #


async def test_two_corrections_typed_in_one_turn_both_arrive_in_order() -> None:
    # Delivering them one iteration apart would let the model act on the first while the
    # second was still invisible — which is exactly the failure being fixed.
    steering = Steering()
    model = FakeModel([tool_turn(call("t1", "read", path="a.py")), text_turn("ok")])
    tools = FakeTools(steering_tool(steering, "use pathlib", "and no new deps"))

    await drive(run_turn(seed(), model, tools, FakePolicy(), steering=steering.drain))

    assert kinds(model.calls[1].messages)[-2:] == ["steer", "steer"]
    assert [texts(m) for m in model.calls[1].messages[-2:]] == ["use pathlib", "and no new deps"]


async def test_a_steer_is_delivered_once_and_not_again_on_the_next_iteration() -> None:
    steering = Steering()
    model = FakeModel(
        [
            tool_turn(call("t1", "read", path="a.py")),
            tool_turn(call("t2", "read", path="b.py")),
            text_turn("done"),
        ]
    )
    calls: list[int] = []

    def handle(arguments: Mapping[str, Any]) -> ToolResult:
        del arguments
        calls.append(1)
        if len(calls) == 1:
            steering.push("only say it once")
        return ToolResult(ok=True, content="read")

    tools = FakeTools({"read": (spec("read"), handle)})

    await drive(run_turn(seed(), model, tools, FakePolicy(), steering=steering.drain))

    assert kinds(model.calls[1].messages).count("steer") == 1
    assert kinds(model.calls[2].messages).count("steer") == 1
    assert steering.pending() == ()


async def test_a_correction_left_over_from_before_the_turn_lands_before_the_first_call() -> None:
    # The holder is session-scoped, so a message that missed the previous turn is
    # delivered by this one rather than sitting there forever.
    steering = Steering()
    steering.push("left over from last time")
    model = FakeModel([text_turn("noted")])
    tools = FakeTools({})

    await drive(run_turn(seed(), model, tools, FakePolicy(), steering=steering.drain))

    assert kinds(model.calls[0].messages) == ["user", "steer"]


# --------------------------------------------------------------------------- #
# The two ways it must stay out of the way
# --------------------------------------------------------------------------- #


async def test_without_a_steering_channel_the_transcript_is_untouched() -> None:
    model = FakeModel([tool_turn(call("t1", "read", path="a.py")), text_turn("done")])
    tools = FakeTools({"read": (spec("read"), succeeds("a.py has 12 lines"))})

    await drive(run_turn(seed(), model, tools, FakePolicy()))

    assert kinds(model.calls[1].messages) == ["user", "assistant_calls", "tool_result"]


async def test_an_interrupted_turn_holds_the_correction_rather_than_swallowing_it() -> None:
    # The drain is *after* the cancellation check on purpose. A turn that is ending has
    # nothing to steer, and taking the message there would lose it: leaving it in the
    # holder is what lets the orchestrator deliver it as the next turn.
    steering = Steering()
    policy = FakePolicy()
    model = FakeModel([tool_turn(call("t1", "read", path="a.py")), text_turn("unreached")])

    def handle(arguments: Mapping[str, Any]) -> ToolResult:
        del arguments
        steering.push("do it the other way")
        policy.cancel()
        return ToolResult(ok=True, content="read it")

    tools = FakeTools({"read": (spec("read"), handle)})

    events = await drive(run_turn(seed(), model, tools, policy, steering=steering.drain))

    ends = [event for event in events if isinstance(event, TurnEnd)]
    assert ends[-1].stop_reason == "interrupted"
    assert len(model.calls) == 1
    assert steering.pending() == ("do it the other way",)


async def test_a_turn_that_never_starts_does_not_take_the_correction_with_it() -> None:
    # The narrow case that pins the drain's position relative to the cancellation check
    # rather than merely to the tool run: the policy is already cancelled, so iteration 0
    # ends before the model is ever called. Draining first would consume the message into
    # a turn that produced nothing.
    steering = Steering()
    steering.push("try the other approach")
    policy = FakePolicy()
    policy.cancel()
    model = FakeModel([text_turn("unreached")])

    events = await drive(run_turn(seed(), model, FakeTools({}), policy, steering=steering.drain))

    ends = [event for event in events if isinstance(event, TurnEnd)]
    assert ends[-1].stop_reason == "interrupted"
    assert model.calls == []
    assert steering.pending() == ("try the other approach",)
