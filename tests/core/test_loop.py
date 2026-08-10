"""Behaviour tests for the ReAct loop.

Everything the loop touches arrives through a protocol, so every test here runs
offline against the doubles in ``fakes.py``: no network, no provider, and nothing
reaching inside :mod:`ronin.core.loop` to patch it. What the loop *sent* is
asserted from the doubles' recordings; what it *said* is asserted from the event
stream; what it *accumulated* is asserted from ``TurnEnd.agent_state``.

The pairing invariant (every ``ToolUse`` answered by exactly one
``ToolResultBlock``) is the one rule checked after every reachable stop
condition — see ``test_the_transcript_stays_paired_at_every_reachable_stop`` —
because a transcript that violates it is one a provider rejects on resume.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest
from fakes import (
    WATCHDOG_SECONDS,
    FakeModel,
    FakePolicy,
    FakeTools,
    assistant_calls,
    call,
    explodes,
    seed,
    spec,
    succeeds,
    text_turn,
    tool_turn,
    user,
    yield_to_loop,
)

from ronin.core.loop import (
    INTERRUPTED_ERROR,
    STALL_ABORTED,
    StalledError,
    StopReason,
    fingerprint,
    run_turn,
    truncate_for_model,
)
from ronin.core.protocols import (
    FinalMessage,
    ModelChunk,
    ModelClient,
    Policy,
    ResetChunk,
    TextChunk,
    ToolRegistry,
)
from ronin.core.types import (
    AgentState,
    ApprovalRequest,
    DangerLevel,
    Error,
    Event,
    Message,
    Mode,
    Role,
    StreamReset,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolResultBlock,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
    unpaired_tool_uses,
)

# --------------------------------------------------------------------------- #
# Drain + assertion helpers
# --------------------------------------------------------------------------- #


async def drain(events: AsyncIterator[Event]) -> list[Event]:
    """Collect a whole turn. Every test needs this, so it lives in one place."""
    return [event async for event in events]


async def run_events(
    model: FakeModel,
    tools: FakeTools,
    policy: FakePolicy,
    *,
    state: AgentState | None = None,
    **kwargs: Any,
) -> list[Event]:
    return await drain(
        run_turn(state if state is not None else seed(), model, tools, policy, **kwargs)
    )


def only_turn_end(events: list[Event]) -> TurnEnd:
    ends = [event for event in events if isinstance(event, TurnEnd)]
    assert len(ends) == 1, f"a turn emits exactly one TurnEnd, got {len(ends)}"
    return ends[0]


def ended_state(events: list[Event]) -> AgentState:
    end = only_turn_end(events)
    assert end.agent_state is not None, "TurnEnd must carry the state to resume from"
    return end.agent_state


def of_type(events: list[Event], kind: type) -> list[Any]:
    return [event for event in events if isinstance(event, kind)]


def tool_blocks(state: AgentState) -> tuple[ToolResultBlock, ...]:
    """The result blocks of the first tool-role message in a transcript."""
    return next(m for m in state.messages if m.role is Role.TOOL).tool_results


def nudges(state: AgentState) -> list[Message]:
    """Every system-role message the loop injected — i.e. every stall nudge."""
    return [m for m in state.messages if m.role is Role.SYSTEM]


READER = spec("read_file")
GREPPER = spec("grep")
EDITOR = spec("edit_file", danger=DangerLevel.MUTATING)
GATED = spec("deploy", danger=DangerLevel.MUTATING, approval=True)


def one_reader(content: str = "file contents") -> FakeTools:
    return FakeTools({"read_file": (READER, succeeds(content))})


# --------------------------------------------------------------------------- #
# The doubles really are the seams
# --------------------------------------------------------------------------- #


def test_the_doubles_satisfy_the_injected_protocols() -> None:
    # The protocols are runtime_checkable, so structural conformance is a
    # cheap assertion — and if it fails, every other test's failure is noise.
    assert isinstance(FakeModel([]), ModelClient)
    assert isinstance(one_reader(), ToolRegistry)
    assert isinstance(FakePolicy(), Policy)


# --------------------------------------------------------------------------- #
# Streaming: the happy path
# --------------------------------------------------------------------------- #


async def test_text_chunks_arrive_as_text_deltas_in_order() -> None:
    model = FakeModel([text_turn("Hel", "lo, ", "world")])
    events = await run_events(model, one_reader(), FakePolicy())
    assert [delta.text for delta in of_type(events, TextDelta)] == [
        "Hel",
        "lo, ",
        "world",
    ]


async def test_a_thinking_chunk_keeps_its_thinking_flag() -> None:
    model = FakeModel(
        [
            (
                TextChunk(text="let me think", thinking=True),
                TextChunk(text="the answer"),
                FinalMessage(message=assistant_calls(text="the answer")),
            )
        ]
    )
    events = await run_events(model, one_reader(), FakePolicy())
    assert [(d.text, d.thinking) for d in of_type(events, TextDelta)] == [
        ("let me think", True),
        ("the answer", False),
    ]


async def test_a_reset_chunk_becomes_a_stream_reset_event() -> None:
    # A provider retry after tokens were already sent; the consumer must be told
    # to discard what it rendered rather than render the answer twice.
    model = FakeModel(
        [
            (
                TextChunk(text="half an ans"),
                ResetChunk(reason="provider retry"),
                TextChunk(text="the real answer"),
                FinalMessage(message=assistant_calls(text="the real answer")),
            )
        ]
    )
    events = await run_events(model, one_reader(), FakePolicy())
    resets = of_type(events, StreamReset)
    assert [reset.reason for reset in resets] == ["provider retry"]
    assert isinstance(events[2], StreamReset), "the reset must land between the deltas"


async def test_a_turn_starts_with_a_turn_start_for_each_iteration() -> None:
    model = FakeModel([tool_turn(call("t1", "read_file", path="a.py")), text_turn("ok")])
    events = await run_events(model, one_reader(), FakePolicy())
    assert [start.turn_index for start in of_type(events, TurnStart)] == [0, 1]
    assert of_type(events, TurnStart)[0].state is TurnState.THINKING


# --------------------------------------------------------------------------- #
# Stop reasons — one test each
# --------------------------------------------------------------------------- #


async def test_a_final_message_with_no_tool_calls_ends_the_turn_done() -> None:
    events = await run_events(FakeModel([text_turn("all done")]), one_reader(), FakePolicy())
    end = only_turn_end(events)
    assert (end.state, end.stop_reason) == (
        TurnState.DONE,
        StopReason.NO_TOOL_CALLS.value,
    )


async def test_exhausting_the_iteration_budget_ends_the_turn_in_error() -> None:
    # Distinct arguments so the stall detector stays out of it — this test is
    # about the iteration ceiling, nothing else.
    model = FakeModel(
        [
            tool_turn(call("t1", "read_file", path="a.py")),
            tool_turn(call("t2", "read_file", path="b.py")),
        ]
    )
    events = await run_events(model, one_reader(), FakePolicy(), max_iterations=2)
    end = only_turn_end(events)
    assert (end.turn_index, end.state, end.stop_reason) == (
        1,
        TurnState.ERROR,
        StopReason.MAX_ITERATIONS.value,
    )


async def test_a_token_budget_stop_reason_ends_the_turn_before_the_model_is_called() -> None:
    model = FakeModel([text_turn("never reached")])
    events = await run_events(model, one_reader(), FakePolicy(budget_reason="token_budget"))
    end = only_turn_end(events)
    assert (end.state, end.stop_reason) == (TurnState.DONE, StopReason.TOKEN_BUDGET.value)
    assert model.calls == [], "a stopped budget must not spend another request"


async def test_a_cost_budget_stop_reason_ends_the_turn() -> None:
    model = FakeModel([text_turn("never reached")])
    events = await run_events(model, one_reader(), FakePolicy(budget_reason="cost_budget"))
    end = only_turn_end(events)
    assert (end.state, end.stop_reason) == (TurnState.DONE, StopReason.COST_BUDGET.value)


async def test_cancellation_before_the_model_call_ends_the_turn_interrupted() -> None:
    model = FakeModel([text_turn("never reached")])
    events = await run_events(model, one_reader(), FakePolicy(cancel_after=0))
    end = only_turn_end(events)
    assert (end.state, end.stop_reason) == (
        TurnState.INTERRUPTED,
        StopReason.INTERRUPTED.value,
    )
    assert model.calls == []


async def test_a_stall_is_raised_rather_than_reported_as_a_stop_reason() -> None:
    # StopReason.STALLED exists but no TurnEnd ever carries it: the loop raises
    # StalledError instead, because a stall is a failure of the run and not an
    # outcome of the turn. Asserted here so the discrepancy is pinned, not lost.
    repeated = [tool_turn(call(f"t{n}", "read_file", path="a.py")) for n in range(4)]
    with pytest.raises(StalledError) as caught:
        await run_events(FakeModel(repeated), one_reader(), FakePolicy())
    assert StopReason.STALLED.value == "stalled"
    assert caught.value.repeats == 4
    assert caught.value.fingerprint == fingerprint(call("t0", "read_file", path="a.py"))


async def test_no_turn_end_is_emitted_when_a_stall_aborts_the_turn() -> None:
    # The one stop the caller learns about from an exception rather than an
    # event, so a consumer that only watches the stream must not assume a
    # TurnEnd always arrives.
    events: list[Event] = []
    turn = run_turn(seed(), _repeat_then_stop(4), one_reader(), FakePolicy())
    with pytest.raises(StalledError):
        async for event in turn:
            events.append(event)
    assert [e for e in events if isinstance(e, TurnEnd)] == []


# --------------------------------------------------------------------------- #
# Tool round trip
# --------------------------------------------------------------------------- #


async def test_a_tool_call_is_bracketed_by_tool_start_and_tool_end() -> None:
    model = FakeModel([tool_turn(call("use-1", "read_file", path="a.py")), text_turn("done")])
    events = await run_events(model, one_reader("hello"), FakePolicy())
    start = of_type(events, ToolStart)[0]
    end = of_type(events, ToolEnd)[0]
    assert (start.tool_use_id, start.name, dict(start.arguments)) == (
        "use-1",
        "read_file",
        {"path": "a.py"},
    )
    assert (end.tool_use_id, end.result) == ("use-1", ToolResult(ok=True, content="hello"))


async def test_the_tool_result_lands_in_the_transcript_paired_to_its_call() -> None:
    model = FakeModel([tool_turn(call("use-1", "read_file", path="a.py")), text_turn("done")])
    events = await run_events(model, one_reader("hello"), FakePolicy())
    blocks = tool_blocks(ended_state(events))
    assert len(blocks) == 1
    block = blocks[0]
    assert (block.tool_use_id, block.content, block.is_error) == ("use-1", "hello", False)


async def test_the_assistant_message_and_tool_results_accumulate_in_order() -> None:
    model = FakeModel(
        [
            tool_turn(call("use-1", "read_file", path="a.py"), text="reading it"),
            text_turn("it says hello"),
        ]
    )
    events = await run_events(model, one_reader("hello"), FakePolicy(), state=seed("read a.py"))
    messages = ended_state(events).messages
    assert [m.role for m in messages] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    ]
    assert (messages[1].text, messages[3].text) == ("reading it", "it says hello")


async def test_the_model_sees_the_tool_result_on_the_next_call() -> None:
    # The loop's whole job between iterations: feed the observation back.
    model = FakeModel([tool_turn(call("use-1", "read_file", path="a.py")), text_turn("done")])
    await run_events(model, one_reader("hello"), FakePolicy())
    assert model.calls[0].roles == (Role.USER,)
    assert model.calls[1].roles == (Role.USER, Role.ASSISTANT, Role.TOOL)


async def test_the_loop_hands_the_system_prompt_and_tool_specs_to_the_model() -> None:
    tools = FakeTools({"read_file": (READER, succeeds("x")), "grep": (GREPPER, succeeds("y"))})
    model = FakeModel([text_turn("ok")])
    await run_events(model, tools, FakePolicy(), system="be terse")
    assert model.calls[0].system == "be terse"
    assert model.calls[0].tools == (READER, GREPPER)


async def test_reported_usage_folds_into_the_budget_carried_out_on_turn_end() -> None:
    model = FakeModel(
        [
            (
                FinalMessage(
                    message=assistant_calls(text="done"),
                    input_tokens=120,
                    output_tokens=30,
                    cost_usd=0.0025,
                ),
            )
        ]
    )
    budget = ended_state(await run_events(model, one_reader(), FakePolicy())).budget
    assert (budget.spent_tokens, budget.spent_usd) == (150, 0.0025)


# --------------------------------------------------------------------------- #
# Parallel vs serial — proven, not assumed
# --------------------------------------------------------------------------- #


async def test_a_read_only_batch_runs_in_parallel() -> None:
    # A Barrier is the proof: neither handler can return until both have
    # arrived, which is impossible if the loop ran them one after the other.
    # wait_for turns that impossibility into a failed assertion instead of a
    # hung suite.
    barrier = asyncio.Barrier(2)

    async def rendezvous(_arguments: Mapping[str, Any]) -> ToolResult:
        await asyncio.wait_for(barrier.wait(), timeout=WATCHDOG_SECONDS)
        return ToolResult(ok=True, content="arrived")

    tools = FakeTools({"read_file": (READER, rendezvous), "grep": (GREPPER, rendezvous)})
    model = FakeModel(
        [
            tool_turn(call("u1", "read_file", path="a.py"), call("u2", "grep", q="x")),
            text_turn("done"),
        ]
    )
    events = await run_events(model, tools, FakePolicy())
    assert tools.max_in_flight == 2
    assert all(end.result.ok for end in of_type(events, ToolEnd))


async def test_a_mutating_call_in_the_batch_forces_serial_execution() -> None:
    # Both handlers hand control back to the event loop, so if the loop had
    # gathered them the second would enter while the first was suspended and
    # max_in_flight would be 2. It is 1 because one call mutates.
    async def cooperative(_arguments: Mapping[str, Any]) -> ToolResult:
        await yield_to_loop()
        return ToolResult(ok=True, content="ok")

    tools = FakeTools({"read_file": (READER, cooperative), "edit_file": (EDITOR, cooperative)})
    model = FakeModel(
        [
            tool_turn(call("u1", "read_file", path="a.py"), call("u2", "edit_file", path="a.py")),
            text_turn("done"),
        ]
    )
    await run_events(model, tools, FakePolicy())
    assert tools.max_in_flight == 1
    assert tools.executed_names == ("read_file", "edit_file")


async def test_a_single_read_only_call_is_not_gathered() -> None:
    async def cooperative(_arguments: Mapping[str, Any]) -> ToolResult:
        await yield_to_loop()
        return ToolResult(ok=True, content="ok")

    tools = FakeTools({"read_file": (READER, cooperative)})
    model = FakeModel([tool_turn(call("u1", "read_file", path="a.py")), text_turn("done")])
    await run_events(model, tools, FakePolicy())
    assert tools.max_in_flight == 1


async def test_parallel_results_stay_paired_to_the_calls_that_asked_for_them() -> None:
    # Completion order under gather is not call order, so the transcript has to
    # be re-ordered by the loop — a mismatched pairing here is a provider 400.
    slow = asyncio.Event()

    async def waits(_arguments: Mapping[str, Any]) -> ToolResult:
        await slow.wait()
        return ToolResult(ok=True, content="slow")

    async def releases(_arguments: Mapping[str, Any]) -> ToolResult:
        slow.set()
        return ToolResult(ok=True, content="fast")

    tools = FakeTools({"read_file": (READER, waits), "grep": (GREPPER, releases)})
    model = FakeModel(
        [
            tool_turn(call("u1", "read_file", path="a.py"), call("u2", "grep", q="x")),
            text_turn("done"),
        ]
    )
    events = await run_events(model, tools, FakePolicy())
    assert tools.completions == ["grep", "read_file"], "the fast tool finished first"
    blocks = tool_blocks(ended_state(events))
    assert [(b.tool_use_id, b.content) for b in blocks] == [
        ("u1", "slow"),
        ("u2", "fast"),
    ]


# --------------------------------------------------------------------------- #
# Approval
# --------------------------------------------------------------------------- #


async def test_a_gated_tool_emits_an_approval_request_showing_what_will_run() -> None:
    tools = FakeTools({"deploy": (GATED, succeeds("deployed"))})
    model = FakeModel([tool_turn(call("u1", "deploy", env="prod")), text_turn("done")])
    events = await run_events(model, tools, FakePolicy())
    request = of_type(events, ApprovalRequest)[0]
    assert (request.tool_use_id, request.danger_level, request.rendered) == (
        "u1",
        DangerLevel.MUTATING,
        'deploy({"env": "prod"})',
    )


async def test_a_gated_tool_asks_the_policy_with_the_same_rendering() -> None:
    tools = FakeTools({"deploy": (GATED, succeeds("deployed"))})
    model = FakeModel([tool_turn(call("u1", "deploy", env="prod")), text_turn("done")])
    policy = FakePolicy()
    events = await run_events(model, tools, policy)
    assert len(policy.approvals) == 1
    ask = policy.approvals[0]
    assert (ask.spec, ask.use.id) == (GATED, "u1")
    # What the human was shown is what the policy was asked about — one string.
    assert ask.rendered == of_type(events, ApprovalRequest)[0].rendered


async def test_an_approved_tool_runs() -> None:
    tools = FakeTools({"deploy": (GATED, succeeds("deployed"))})
    model = FakeModel([tool_turn(call("u1", "deploy", env="prod")), text_turn("done")])
    events = await run_events(model, tools, FakePolicy(approve_all=True))
    assert tools.executed_names == ("deploy",)
    assert of_type(events, ToolEnd)[0].result == ToolResult(ok=True, content="deployed")


async def test_a_denied_tool_is_never_executed_and_comes_back_as_a_denied_error() -> None:
    tools = FakeTools({"deploy": (GATED, succeeds("deployed"))})
    model = FakeModel([tool_turn(call("u1", "deploy", env="prod")), text_turn("done")])
    events = await run_events(
        model, tools, FakePolicy(approve_all=False, deny_reason="not on a Friday")
    )
    result = of_type(events, ToolEnd)[0].result
    assert (result.ok, result.error) == (False, "DENIED: not on a Friday")
    assert tools.executions == [], "a denied tool must not run at all"


async def test_an_ungated_tool_never_reaches_the_policy() -> None:
    tools = one_reader()
    model = FakeModel([tool_turn(call("u1", "read_file", path="a.py")), text_turn("done")])
    policy = FakePolicy()
    await run_events(model, tools, policy)
    assert policy.approvals == []
    assert tools.executed_names == ("read_file",)


async def test_a_denial_still_leaves_a_paired_transcript() -> None:
    tools = FakeTools({"deploy": (GATED, succeeds("deployed"))})
    model = FakeModel([tool_turn(call("u1", "deploy", env="prod")), text_turn("done")])
    events = await run_events(model, tools, FakePolicy(approve_all=False))
    block = tool_blocks(ended_state(events))[0]
    assert (block.tool_use_id, block.is_error) == ("u1", True)


# --------------------------------------------------------------------------- #
# Errors are values
# --------------------------------------------------------------------------- #


async def test_a_tool_that_raises_becomes_a_failed_tool_result() -> None:
    tools = FakeTools({"read_file": (READER, explodes(FileNotFoundError("a.py")))})
    model = FakeModel([tool_turn(call("u1", "read_file", path="a.py")), text_turn("recovering")])
    events = await run_events(model, tools, FakePolicy())
    result = of_type(events, ToolEnd)[0].result
    assert (result.ok, result.error) == (False, "FileNotFoundError: a.py")
    # The turn continues: the exception became an observation, not a crash.
    assert only_turn_end(events).stop_reason == StopReason.NO_TOOL_CALLS.value


async def test_a_tool_that_raises_inside_a_parallel_batch_becomes_a_value_too() -> None:
    tools = FakeTools(
        {
            "read_file": (READER, explodes(ValueError("bad path"))),
            "grep": (GREPPER, succeeds("match")),
        }
    )
    model = FakeModel(
        [
            tool_turn(call("u1", "read_file", path="a.py"), call("u2", "grep", q="x")),
            text_turn("done"),
        ]
    )
    events = await run_events(model, tools, FakePolicy())
    assert [(e.tool_use_id, e.result.ok, e.result.error) for e in of_type(events, ToolEnd)] == [
        ("u1", False, "ValueError: bad path"),
        ("u2", True, ""),
    ]


async def test_a_tool_cancelled_inside_a_parallel_batch_reports_the_interruption() -> None:
    # gather(return_exceptions=True) hands a CancelledError back as a *result*,
    # so the loop's dedicated branch for it is reachable: the call must still be
    # answered, or the transcript loses its pairing.
    tools = FakeTools(
        {
            "read_file": (READER, explodes(asyncio.CancelledError())),
            "grep": (GREPPER, succeeds("match")),
        }
    )
    model = FakeModel(
        [
            tool_turn(call("u1", "read_file", path="a.py"), call("u2", "grep", q="x")),
            text_turn("done"),
        ]
    )
    events = await run_events(model, tools, FakePolicy())
    assert of_type(events, ToolEnd)[0].result == ToolResult(ok=False, error=INTERRUPTED_ERROR)
    assert unpaired_tool_uses(ended_state(events).messages) == ()


async def test_a_hallucinated_tool_name_is_reported_without_executing_anything() -> None:
    tools = one_reader()
    model = FakeModel([tool_turn(call("u1", "definitely_not_a_tool", x=1)), text_turn("sorry")])
    events = await run_events(model, tools, FakePolicy())
    result = of_type(events, ToolEnd)[0].result
    assert result.ok is False
    assert "not registered" in result.error
    assert tools.executions == []


async def test_a_stream_with_no_final_message_errors_the_turn() -> None:
    model = FakeModel([(TextChunk(text="half an answer"),)])
    events = await run_events(model, one_reader(), FakePolicy())
    error = of_type(events, Error)[0]
    assert (error.kind, error.recoverable) == ("protocol", False)
    end = only_turn_end(events)
    assert (end.state, end.stop_reason) == (TurnState.ERROR, "protocol_error")
    assert events.index(error) < events.index(end), "the Error precedes the TurnEnd"


# --------------------------------------------------------------------------- #
# Interruption mid-tool
# --------------------------------------------------------------------------- #


def _interrupt_mid_batch() -> tuple[FakeModel, FakeTools, FakePolicy]:
    """Two MUTATING calls where the first handler flips cancellation.

    MUTATING rather than READ_ONLY on purpose: the serial path is the one that
    polls ``policy.cancelled()`` between calls, so it is the path where
    "interrupt mid-tool" is observable. See the note in the final report about
    the parallel path.
    """
    policy = FakePolicy()
    second = spec("edit_b", danger=DangerLevel.MUTATING)

    def cancel_then_succeed(_arguments: Mapping[str, Any]) -> ToolResult:
        policy.cancel()
        return ToolResult(ok=True, content="edited a")

    tools = FakeTools(
        {
            "edit_file": (EDITOR, cancel_then_succeed),
            "edit_b": (second, succeeds("edited b")),
        }
    )
    model = FakeModel([tool_turn(call("u1", "edit_file", path="a"), call("u2", "edit_b"))])
    return model, tools, policy


async def test_cancelling_mid_batch_gives_the_remaining_calls_an_interrupted_result() -> None:
    events = await run_events(*_interrupt_mid_batch())
    assert [(e.tool_use_id, e.result) for e in of_type(events, ToolEnd)] == [
        ("u1", ToolResult(ok=True, content="edited a")),
        ("u2", ToolResult(ok=False, error=INTERRUPTED_ERROR)),
    ]


async def test_cancelling_mid_batch_does_not_run_the_remaining_tools() -> None:
    model, tools, policy = _interrupt_mid_batch()
    await run_events(model, tools, policy)
    assert tools.executed_names == ("edit_file",)


async def test_an_interruption_mid_batch_ends_the_turn_interrupted() -> None:
    events = await run_events(*_interrupt_mid_batch())
    end = only_turn_end(events)
    assert (end.state, end.stop_reason) == (
        TurnState.INTERRUPTED,
        StopReason.INTERRUPTED.value,
    )


async def test_an_interrupted_turn_carries_a_resumable_agent_state() -> None:
    start = AgentState(messages=(user("edit a"),), cwd="/work", mode=Mode.AUTO_EDIT)
    model, tools, policy = _interrupt_mid_batch()
    events = await run_events(model, tools, policy, state=start)
    ended = ended_state(events)
    assert isinstance(ended, AgentState)
    # Resumable means: nothing the caller set was dropped, and the transcript is
    # sendable — the interrupted call was still answered.
    assert (ended.cwd, ended.mode) == ("/work", Mode.AUTO_EDIT)
    assert unpaired_tool_uses(ended.messages) == ()


async def test_the_state_from_an_interrupted_turn_can_start_another_turn() -> None:
    model, tools, policy = _interrupt_mid_batch()
    interrupted = ended_state(await run_events(model, tools, policy))

    resumed = FakeModel([text_turn("picking up where we left off")])
    events = await run_events(resumed, tools, FakePolicy(), state=interrupted)
    assert only_turn_end(events).stop_reason == StopReason.NO_TOOL_CALLS.value
    # The resumed request replays the interrupted transcript verbatim, plus nothing.
    assert resumed.calls[0].messages == interrupted.messages


# --------------------------------------------------------------------------- #
# Stall detection
# --------------------------------------------------------------------------- #


def _repeat_then_stop(times: int) -> FakeModel:
    """``times`` identical calls, then a plain answer that ends the turn."""
    return FakeModel(
        [tool_turn(call(f"u{n}", "read_file", path="a.py")) for n in range(times)]
        + [text_turn("giving up")]
    )


async def test_repeating_the_same_action_appends_a_system_stall_nudge() -> None:
    events = await run_events(_repeat_then_stop(3), one_reader(), FakePolicy())
    injected = nudges(ended_state(events))
    assert len(injected) == 1
    assert "repeated the same action" in injected[0].text
    assert injected[0].metadata["kind"] == "stall_nudge"


async def test_the_nudge_names_the_action_it_is_about() -> None:
    events = await run_events(_repeat_then_stop(3), one_reader(), FakePolicy())
    nudge = next(m for m in ended_state(events).messages if m.role is Role.SYSTEM)
    assert nudge.metadata["fingerprint"] == fingerprint(call("u0", "read_file", path="a.py"))


async def test_two_repeats_are_not_yet_a_stall() -> None:
    events = await run_events(_repeat_then_stop(2), one_reader(), FakePolicy())
    assert nudges(ended_state(events)) == []


async def test_repeating_after_the_nudge_raises_stalled_error() -> None:
    # The nudge is the loop's one chance to break the cycle; ignoring it is a
    # failure of the run, so it escapes as an exception rather than a TurnEnd.
    with pytest.raises(StalledError, match="after a nudge"):
        await run_events(_repeat_then_stop(4), one_reader(), FakePolicy())


async def test_a_stall_abort_answers_the_calls_it_never_ran() -> None:
    # The aborting batch is not executed, so without a synthetic answer the
    # checkpoint the error carries would be a transcript no provider accepts.
    with pytest.raises(StalledError) as caught:
        await run_events(_repeat_then_stop(4), one_reader(), FakePolicy())
    aborted = caught.value.agent_state
    assert aborted is not None
    assert [
        (block.tool_use_id, block.content, block.is_error)
        for block in aborted.messages[-1].tool_results
    ] == [("u3", STALL_ABORTED, True)]


async def test_a_stall_abort_still_carries_a_resumable_checkpoint() -> None:
    with pytest.raises(StalledError) as caught:
        await run_events(_repeat_then_stop(4), one_reader(), FakePolicy(), state=seed("go"))
    aborted = caught.value.agent_state
    assert aborted is not None
    assert unpaired_tool_uses(aborted.messages) == ()
    assert aborted.messages[0] == user("go")


async def test_a_stall_abort_does_not_execute_the_repeated_call_again() -> None:
    tools = one_reader()
    with pytest.raises(StalledError):
        await run_events(_repeat_then_stop(4), tools, FakePolicy())
    # Three iterations ran the tool; the fourth aborted before executing it.
    assert tools.executed_names == ("read_file", "read_file", "read_file")


async def test_alternating_actions_do_not_trip_the_stall_detector() -> None:
    model = FakeModel(
        [
            tool_turn(call("u1", "read_file", path="a.py")),
            tool_turn(call("u2", "read_file", path="b.py")),
            tool_turn(call("u3", "read_file", path="a.py")),
            tool_turn(call("u4", "read_file", path="b.py")),
            text_turn("done"),
        ]
    )
    events = await run_events(model, one_reader(), FakePolicy())
    assert nudges(ended_state(events)) == []
    assert only_turn_end(events).stop_reason == StopReason.NO_TOOL_CALLS.value


def test_fingerprint_ignores_argument_order() -> None:
    assert fingerprint(call("t1", "grep", a=1, b=2)) == fingerprint(call("t2", "grep", b=2, a=1))


def test_fingerprint_separates_different_actions() -> None:
    assert fingerprint(call("t1", "grep", q="x")) != fingerprint(call("t1", "grep", q="y"))
    assert fingerprint(call("t1", "grep", q="x")) != fingerprint(call("t1", "read_file", q="x"))


def test_fingerprint_survives_unserializable_arguments() -> None:
    # `default=repr` keeps stall detection working on whatever a provider hands
    # back, instead of raising inside the detector.
    marker = object()
    assert fingerprint(call("t1", "grep", value=marker)) == fingerprint(
        call("t2", "grep", value=marker)
    )


# --------------------------------------------------------------------------- #
# Truncation
# --------------------------------------------------------------------------- #


def test_truncate_for_model_reports_the_chars_and_lines_it_cut() -> None:
    content = "a" * 10 + "\n" + "b" * 10
    assert truncate_for_model(content, 5) == "aaaaa\n…[truncated: 16 chars, 1 lines cut]"


def test_content_exactly_at_the_limit_is_untouched() -> None:
    # The boundary matters: a marker on an intact result would tell the model the
    # output was cut when it was not.
    assert truncate_for_model("x" * 10, 10) == "x" * 10
    assert truncate_for_model("x" * 9, 10) == "x" * 9


@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_limit_disables_truncation(limit: int) -> None:
    assert truncate_for_model("x" * 100, limit) == "x" * 100


def test_truncation_is_deterministic() -> None:
    content = "line\n" * 40
    assert truncate_for_model(content, 50) == truncate_for_model(content, 50)


async def test_a_long_tool_result_reaches_the_transcript_truncated() -> None:
    content = "line\n" * 40  # 200 chars, 40 newlines
    tools = one_reader(content)
    model = FakeModel([tool_turn(call("u1", "read_file", path="a.py")), text_turn("done")])
    events = await run_events(model, tools, FakePolicy(), max_tool_result_chars=50)
    block = tool_blocks(ended_state(events))[0]
    assert block.content == "line\n" * 10 + "\n…[truncated: 150 chars, 30 lines cut]"


async def test_the_tool_end_event_carries_the_untruncated_result() -> None:
    # Truncation is for the model's context window, not for the UI: the event
    # stream keeps the full text so a consumer can still show all of it.
    content = "line\n" * 40
    model = FakeModel([tool_turn(call("u1", "read_file", path="a.py")), text_turn("done")])
    events = await run_events(model, one_reader(content), FakePolicy(), max_tool_result_chars=50)
    assert of_type(events, ToolEnd)[0].result.content == content


async def test_a_long_error_is_truncated_too() -> None:
    tools = FakeTools({"read_file": (READER, explodes(RuntimeError("z" * 200)))})
    model = FakeModel([tool_turn(call("u1", "read_file", path="a.py")), text_turn("done")])
    events = await run_events(model, tools, FakePolicy(), max_tool_result_chars=40)
    block = tool_blocks(ended_state(events))[0]
    assert block.is_error is True
    assert block.content.endswith(" lines cut]")


# --------------------------------------------------------------------------- #
# The pairing invariant, at every stop the loop can reach
# --------------------------------------------------------------------------- #

PAIRING_SCENARIOS = [
    "no_tool_calls",
    "tool_round_trip",
    "max_iterations",
    "budget",
    "interrupted_before_model",
    "interrupted_mid_batch",
    "denied",
    "unregistered",
    "tool_raised",
    "parallel_batch",
    "stall_nudge",
    "protocol_error",
]


def _scenario(
    name: str,
) -> tuple[FakeModel, FakeTools, FakePolicy, dict[str, Any]]:
    reader = one_reader()
    round_trip: list[tuple[ModelChunk, ...]] = [
        tool_turn(call("u1", "read_file", path="a.py")),
        text_turn("done"),
    ]
    if name == "no_tool_calls":
        return FakeModel([text_turn("done")]), reader, FakePolicy(), {}
    if name == "tool_round_trip":
        return FakeModel(round_trip), reader, FakePolicy(), {}
    if name == "max_iterations":
        return (
            FakeModel(
                [
                    tool_turn(call("u1", "read_file", path="a.py")),
                    tool_turn(call("u2", "read_file", path="b.py")),
                ]
            ),
            reader,
            FakePolicy(),
            {"max_iterations": 2},
        )
    if name == "budget":
        return FakeModel(round_trip), reader, FakePolicy(budget_reason="token_budget"), {}
    if name == "interrupted_before_model":
        return FakeModel(round_trip), reader, FakePolicy(cancel_after=0), {}
    if name == "interrupted_mid_batch":
        model, tools, policy = _interrupt_mid_batch()
        return model, tools, policy, {}
    if name == "denied":
        gated = FakeTools({"deploy": (GATED, succeeds("deployed"))})
        return (
            FakeModel([tool_turn(call("u1", "deploy", env="prod")), text_turn("ok")]),
            gated,
            FakePolicy(approve_all=False),
            {},
        )
    if name == "unregistered":
        return (
            FakeModel([tool_turn(call("u1", "nope")), text_turn("ok")]),
            reader,
            FakePolicy(),
            {},
        )
    if name == "tool_raised":
        boom = FakeTools({"read_file": (READER, explodes(RuntimeError("boom")))})
        return FakeModel(round_trip), boom, FakePolicy(), {}
    if name == "parallel_batch":
        both = FakeTools({"read_file": (READER, succeeds("x")), "grep": (GREPPER, succeeds("y"))})
        return (
            FakeModel(
                [
                    tool_turn(call("u1", "read_file", path="a.py"), call("u2", "grep", q="x")),
                    text_turn("done"),
                ]
            ),
            both,
            FakePolicy(),
            {},
        )
    if name == "stall_nudge":
        return _repeat_then_stop(3), reader, FakePolicy(), {}
    if name == "protocol_error":
        return FakeModel([(TextChunk(text="half"),)]), reader, FakePolicy(), {}
    raise AssertionError(f"unknown scenario {name!r}")


@pytest.mark.parametrize("scenario", PAIRING_SCENARIOS)
async def test_the_transcript_stays_paired_at_every_reachable_stop(scenario: str) -> None:
    model, tools, policy, kwargs = _scenario(scenario)
    events = await run_events(model, tools, policy, **kwargs)
    assert unpaired_tool_uses(ended_state(events).messages) == ()


@pytest.mark.parametrize("scenario", PAIRING_SCENARIOS)
async def test_every_stop_carries_a_state_and_a_reason(scenario: str) -> None:
    model, tools, policy, kwargs = _scenario(scenario)
    end = only_turn_end(await run_events(model, tools, policy, **kwargs))
    assert end.stop_reason != "", "a consumer must always be told why the turn ended"
    assert end.state in {TurnState.DONE, TurnState.ERROR, TurnState.INTERRUPTED}


@pytest.mark.parametrize("scenario", PAIRING_SCENARIOS)
async def test_every_stop_preserves_the_messages_it_started_with(scenario: str) -> None:
    # The loop appends; it never rewrites history. A resumed state that lost the
    # user's prompt is worse than no checkpoint at all.
    start = seed("read a.py")
    model, tools, policy, kwargs = _scenario(scenario)
    events = await run_events(model, tools, policy, state=start, **kwargs)
    messages: tuple[Message, ...] = ended_state(events).messages
    assert messages[: len(start.messages)] == start.messages
