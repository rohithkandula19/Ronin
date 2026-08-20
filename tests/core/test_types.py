"""Invariant tests for the core contract.

These assert the *rules*, not behavior — there is no logic to test yet. Every
validator branch, every transition-table entry, and both closed unions are
covered, because a contract that isn't enforced is a comment.
"""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Callable

import pytest

from ronin.core.types import (
    DENY_UNATTENDED,
    EVENT_TYPES,
    RESTING_STATES,
    TRANSITIONS,
    AgentState,
    ApprovalDecision,
    ApprovalRequest,
    Budget,
    Compaction,
    DangerLevel,
    Error,
    IllegalTransition,
    Message,
    Mode,
    Role,
    StreamReset,
    Text,
    TextDelta,
    Thinking,
    Todo,
    TodoStatus,
    ToolEnd,
    ToolOutput,
    ToolResult,
    ToolResultBlock,
    ToolSpec,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
    TurnState,
    VerifyResult,
    assert_transition,
    can_transition,
    is_event,
    unpaired_tool_uses,
)

# --------------------------------------------------------------------------- #
# Content blocks
# --------------------------------------------------------------------------- #


def test_text_requires_a_string() -> None:
    assert Text("hi").text == "hi"
    with pytest.raises(TypeError, match=r"must be a str"):
        Text(42)  # type: ignore[arg-type]


def test_thinking_carries_an_opaque_signature() -> None:
    block = Thinking("because", signature="sig-bytes")
    assert (block.text, block.signature) == ("because", "sig-bytes")
    assert Thinking("x").signature == ""
    with pytest.raises(TypeError, match=r"must be a str"):
        Thinking(None)  # type: ignore[arg-type]


def test_tool_use_requires_id_name_and_mapping_arguments() -> None:
    call = ToolUse(id="t1", name="read_file", arguments={"path": "a.py"})
    assert call.arguments["path"] == "a.py"
    assert ToolUse(id="t1", name="x").arguments == {}

    with pytest.raises(ValueError, match=r"ToolUse.id is required"):
        ToolUse(id="", name="read_file")
    with pytest.raises(ValueError, match=r"ToolUse.name is required"):
        ToolUse(id="t1", name="")
    # A JSON *string* is the provider layer's problem, not the contract's.
    with pytest.raises(TypeError, match=r"must be a mapping"):
        ToolUse(id="t1", name="x", arguments='{"path": "a.py"}')  # type: ignore[arg-type]


def test_tool_result_block_must_answer_some_call() -> None:
    block = ToolResultBlock(tool_use_id="t1", content="ok")
    assert block.is_error is False
    with pytest.raises(ValueError, match=r"tool_use_id is required"):
        ToolResultBlock(tool_use_id="", content="ok")


# --------------------------------------------------------------------------- #
# Message
# --------------------------------------------------------------------------- #


def test_message_rejects_a_bare_string_for_content() -> None:
    with pytest.raises(TypeError, match=r"not a string"):
        Message(role=Role.USER, content_blocks="hello")  # type: ignore[arg-type]


def test_message_requires_a_role_and_a_tuple() -> None:
    with pytest.raises(TypeError, match=r"must be a Role"):
        Message(role="user")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"must be a tuple"):
        Message(role=Role.USER, content_blocks=[Text("hi")])  # type: ignore[arg-type]


def test_message_rejects_unknown_block_types() -> None:
    with pytest.raises(TypeError, match=r"unknown content block type: int"):
        Message(role=Role.USER, content_blocks=(1,))  # type: ignore[arg-type]


@pytest.mark.parametrize("role", [Role.USER, Role.SYSTEM, Role.TOOL])
@pytest.mark.parametrize(
    "block", [ToolUse(id="t1", name="x"), Thinking("reasoning")], ids=["tooluse", "thinking"]
)
def test_only_the_assistant_may_call_tools_or_think(role: Role, block: object) -> None:
    with pytest.raises(ValueError, match=r"may only appear on an assistant"):
        Message(role=role, content_blocks=(block,))  # type: ignore[arg-type]


def test_assistant_may_carry_tool_uses_and_thinking() -> None:
    message = Message(
        role=Role.ASSISTANT,
        content_blocks=(Thinking("plan"), Text("I'll read it."), ToolUse(id="t1", name="read")),
    )
    assert [b.id for b in message.tool_uses] == ["t1"]
    assert message.tool_results == ()
    # Reasoning is deliberately excluded from .text — thinking is not the answer.
    assert message.text == "I'll read it."


def test_message_projections_and_metadata() -> None:
    message = Message(
        role=Role.TOOL,
        content_blocks=(ToolResultBlock(tool_use_id="t1", content="data"),),
        metadata={"latency_ms": 12},
    )
    assert [b.tool_use_id for b in message.tool_results] == ["t1"]
    assert message.tool_uses == ()
    assert message.text == ""
    assert message.metadata["latency_ms"] == 12
    assert Message(role=Role.USER).content_blocks == ()


# --------------------------------------------------------------------------- #
# Pairing
# --------------------------------------------------------------------------- #


def test_unpaired_tool_uses_is_empty_when_every_call_is_answered() -> None:
    transcript = [
        Message(role=Role.ASSISTANT, content_blocks=(ToolUse(id="t1", name="read"),)),
        Message(role=Role.TOOL, content_blocks=(ToolResultBlock("t1", "ok"),)),
    ]
    assert unpaired_tool_uses(transcript) == ()


def test_unpaired_tool_uses_reports_missing_answers_in_order() -> None:
    transcript = [
        Message(
            role=Role.ASSISTANT,
            content_blocks=(ToolUse(id="t1", name="read"), ToolUse(id="t2", name="grep")),
        ),
        Message(role=Role.TOOL, content_blocks=(ToolResultBlock("t2", "ok"),)),
        Message(role=Role.ASSISTANT, content_blocks=(ToolUse(id="t3", name="write"),)),
    ]
    assert unpaired_tool_uses(transcript) == ("t1", "t3")
    assert unpaired_tool_uses([]) == ()


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


def test_danger_levels_are_ordered() -> None:
    assert DangerLevel.READ_ONLY < DangerLevel.MUTATING < DangerLevel.DESTRUCTIVE
    assert DangerLevel.DESTRUCTIVE < DangerLevel.IRREVERSIBLE
    assert DangerLevel.MUTATING >= DangerLevel.MUTATING


def test_tool_spec_happy_path() -> None:
    spec = ToolSpec(
        name="read_file",
        description="Read a file.",
        json_schema={"type": "object"},
        danger_level=DangerLevel.READ_ONLY,
    )
    assert spec.requires_approval is False
    assert spec.json_schema["type"] == "object"


def test_tool_spec_requires_name_description_and_typed_danger() -> None:
    with pytest.raises(ValueError, match=r"name is required"):
        ToolSpec(name="", description="x")
    with pytest.raises(ValueError, match=r"description is required"):
        ToolSpec(name="x", description="")
    with pytest.raises(TypeError, match=r"must be a DangerLevel"):
        ToolSpec(name="x", description="y", danger_level=2)  # type: ignore[arg-type]


@pytest.mark.parametrize("level", [DangerLevel.DESTRUCTIVE, DangerLevel.IRREVERSIBLE])
def test_a_destructive_tool_cannot_opt_out_of_approval(level: DangerLevel) -> None:
    with pytest.raises(ValueError, match=r"cannot opt out of"):
        ToolSpec(name="rm", description="delete", danger_level=level, requires_approval=False)
    # …but may declare it, which is the only legal shape.
    assert ToolSpec(
        name="rm", description="delete", danger_level=level, requires_approval=True
    ).requires_approval


def test_tool_spec_mutating_may_skip_approval() -> None:
    spec = ToolSpec(name="edit", description="edit", danger_level=DangerLevel.MUTATING)
    assert spec.requires_approval is False


def test_tool_result_success_and_failure_are_the_same_shape() -> None:
    ok = ToolResult(ok=True, content="hello", tokens_estimate=3, artifacts=("a.txt",))
    assert (ok.error, ok.artifacts) == ("", ("a.txt",))
    bad = ToolResult(ok=False, error="ENOENT")
    assert (bad.content, bad.tokens_estimate) == ("", 0)


def test_tool_result_rejects_contradictory_or_silent_failures() -> None:
    with pytest.raises(ValueError, match=r"cannot be ok=True and carry an error"):
        ToolResult(ok=True, error="boom")
    with pytest.raises(ValueError, match=r"must say why"):
        ToolResult(ok=False)
    with pytest.raises(ValueError, match=r"tokens_estimate must be >= 0"):
        ToolResult(ok=True, tokens_estimate=-1)
    with pytest.raises(TypeError, match=r"artifacts must be a tuple"):
        ToolResult(ok=True, artifacts=["a.txt"])  # type: ignore[arg-type]


def test_tool_result_projects_into_the_transcript_preserving_the_error_flag() -> None:
    ok_block = ToolResult(ok=True, content="data").as_block("t1")
    assert (ok_block.tool_use_id, ok_block.content, ok_block.is_error) == ("t1", "data", False)

    err_block = ToolResult(ok=False, error="ENOENT").as_block("t2")
    assert (err_block.content, err_block.is_error) == ("ENOENT", True)


# --------------------------------------------------------------------------- #
# Todos, budget, mode
# --------------------------------------------------------------------------- #


def test_todo_invariants() -> None:
    todo = Todo(id="1", subject="write tests")
    assert todo.status is TodoStatus.PENDING
    with pytest.raises(ValueError, match=r"Todo.id is required"):
        Todo(id="", subject="x")
    with pytest.raises(ValueError, match=r"Todo.subject is required"):
        Todo(id="1", subject="")
    with pytest.raises(TypeError, match=r"must be a TodoStatus"):
        Todo(id="1", subject="x", status="pending")  # type: ignore[arg-type]


def test_budget_defaults_are_unbounded_and_never_exhausted() -> None:
    budget = Budget()
    assert (budget.max_tokens, budget.max_usd, budget.max_wall_seconds) == (None, None, None)
    assert budget.exhausted is False


@pytest.mark.parametrize("name", ["max_tokens", "max_usd", "max_wall_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_budget_limits_must_be_positive_or_none(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=rf"Budget.{name} must be positive or None"):
        Budget(**{name: value})  # type: ignore[arg-type]  # name is parametrized


@pytest.mark.parametrize("name", ["spent_tokens", "spent_usd", "elapsed_seconds"])
def test_budget_spend_cannot_be_negative(name: str) -> None:
    with pytest.raises(ValueError, match=rf"Budget.{name} must be >= 0"):
        Budget(**{name: -1})


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"max_tokens": 10, "spent_tokens": 10}, True),
        ({"max_tokens": 10, "spent_tokens": 9}, False),
        ({"max_usd": 1.0, "spent_usd": 1.5}, True),
        ({"max_usd": 1.0, "spent_usd": 0.5}, False),
        ({"max_wall_seconds": 5.0, "elapsed_seconds": 5.0}, True),
        ({"max_wall_seconds": 5.0, "elapsed_seconds": 1.0}, False),
    ],
)
def test_budget_exhaustion_per_dimension(kwargs: dict[str, float], expected: bool) -> None:
    assert Budget(**kwargs).exhausted is expected  # type: ignore[arg-type]


def test_mode_is_a_permissiveness_ladder() -> None:
    assert Mode.PLAN.rank < Mode.ASK.rank < Mode.AUTO_EDIT.rank < Mode.FULL.rank
    assert Mode.FULL.at_least(Mode.ASK)
    assert Mode.ASK.at_least(Mode.ASK)
    assert not Mode.PLAN.at_least(Mode.AUTO_EDIT)


# --------------------------------------------------------------------------- #
# AgentState
# --------------------------------------------------------------------------- #


def test_agent_state_defaults_are_a_valid_empty_session() -> None:
    state = AgentState()
    assert (state.messages, state.todos, state.cwd) == ((), (), ".")
    assert state.mode is Mode.ASK
    assert state.checkpoint_id is None
    assert state.budget.exhausted is False
    assert state.pairing_errors == ()


def test_agent_state_requires_frozen_collections_and_a_real_cwd() -> None:
    with pytest.raises(TypeError, match=r"messages must be a tuple"):
        AgentState(messages=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"todos must be a tuple"):
        AgentState(todos=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"mode must be a Mode"):
        AgentState(mode="ask")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"cwd must be non-empty"):
        AgentState(cwd="")


def test_agent_state_rejects_duplicate_todo_ids() -> None:
    with pytest.raises(ValueError, match=r"duplicate ids"):
        AgentState(todos=(Todo(id="1", subject="a"), Todo(id="1", subject="b")))


def test_agent_state_checkpoint_id_is_none_or_meaningful() -> None:
    assert AgentState(checkpoint_id="ckpt-1").checkpoint_id == "ckpt-1"
    with pytest.raises(ValueError, match=r"checkpoint_id must be None or non-empty"):
        AgentState(checkpoint_id="")


def test_agent_state_surfaces_pairing_errors() -> None:
    state = AgentState(
        messages=(Message(role=Role.ASSISTANT, content_blocks=(ToolUse(id="t1", name="read"),)),)
    )
    assert state.pairing_errors == ("t1",)


def test_with_message_returns_a_new_state_and_never_mutates() -> None:
    state = AgentState()
    grown = state.with_message(Message(role=Role.USER, content_blocks=(Text("hi"),)))
    assert len(grown.messages) == 1
    assert state.messages == ()  # the original is untouched
    assert grown is not state


def test_state_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        AgentState().cwd = "/tmp"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        Text("a").text = "b"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Turn state machine
# --------------------------------------------------------------------------- #


def test_every_state_has_a_transition_entry() -> None:
    assert set(TRANSITIONS) == set(TurnState)


def test_no_transition_targets_an_unknown_state() -> None:
    for source, targets in TRANSITIONS.items():
        assert targets <= set(TurnState), source


def test_the_happy_path_is_walkable() -> None:
    path = [
        TurnState.IDLE,
        TurnState.THINKING,
        TurnState.TOOL_PENDING,
        TurnState.AWAITING_APPROVAL,
        TurnState.TOOL_RUNNING,
        TurnState.OBSERVING,
        TurnState.VERIFYING,
        TurnState.DONE,
        TurnState.IDLE,
    ]
    for source, target in itertools.pairwise(path):
        assert can_transition(source, target), f"{source} -> {target}"


def test_a_tool_may_run_without_approval_when_none_is_required() -> None:
    assert can_transition(TurnState.TOOL_PENDING, TurnState.TOOL_RUNNING)


def test_a_denied_approval_skips_execution_and_goes_straight_to_observing() -> None:
    assert can_transition(TurnState.AWAITING_APPROVAL, TurnState.OBSERVING)
    assert not can_transition(TurnState.AWAITING_APPROVAL, TurnState.THINKING)


def test_observing_may_loop_back_to_thinking() -> None:
    assert can_transition(TurnState.OBSERVING, TurnState.THINKING)


@pytest.mark.parametrize(
    "source",
    [
        TurnState.THINKING,
        TurnState.TOOL_PENDING,
        TurnState.AWAITING_APPROVAL,
        TurnState.TOOL_RUNNING,
        TurnState.OBSERVING,
        TurnState.VERIFYING,
    ],
)
def test_every_active_state_can_fail_or_be_interrupted(source: TurnState) -> None:
    assert can_transition(source, TurnState.ERROR)
    assert can_transition(source, TurnState.INTERRUPTED)


@pytest.mark.parametrize("source", [TurnState.ERROR, TurnState.INTERRUPTED])
def test_error_and_interrupted_are_resumable_not_dead_ends(source: TurnState) -> None:
    assert can_transition(source, TurnState.THINKING)  # resume
    assert can_transition(source, TurnState.DONE)  # give up cleanly
    assert can_transition(source, TurnState.IDLE)  # abandon the turn


def test_idle_and_done_are_narrow() -> None:
    assert TRANSITIONS[TurnState.IDLE] == frozenset({TurnState.THINKING})
    assert TRANSITIONS[TurnState.DONE] == frozenset({TurnState.IDLE})
    assert not can_transition(TurnState.IDLE, TurnState.TOOL_RUNNING)
    assert not can_transition(TurnState.DONE, TurnState.THINKING)


def test_no_state_transitions_to_itself() -> None:
    for source, targets in TRANSITIONS.items():
        assert source not in targets, f"{source} loops to itself"


def test_resting_states_are_the_only_ones_allowed_to_wait() -> None:
    assert set(RESTING_STATES) == {
        TurnState.IDLE,
        TurnState.DONE,
        TurnState.AWAITING_APPROVAL,
    }


def test_assert_transition_returns_the_target_or_raises() -> None:
    assert assert_transition(TurnState.IDLE, TurnState.THINKING) is TurnState.THINKING
    with pytest.raises(IllegalTransition) as excinfo:
        assert_transition(TurnState.IDLE, TurnState.DONE)
    error = excinfo.value
    assert error.source is TurnState.IDLE
    assert error.target is TurnState.DONE
    assert "illegal turn transition idle -> done" in str(error)
    assert "thinking" in str(error)  # names the legal targets


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


def test_turn_start_defaults_to_thinking() -> None:
    event = TurnStart(turn_index=0)
    assert event.state is TurnState.THINKING
    with pytest.raises(ValueError, match=r"turn_index must be >= 0"):
        TurnStart(turn_index=-1)


def test_text_delta_distinguishes_reasoning_from_the_answer() -> None:
    assert TextDelta("hi").thinking is False
    assert TextDelta("hmm", thinking=True).thinking is True


def test_stream_reset_invalidates_rendered_text_and_nothing_else() -> None:
    reset = StreamReset()
    assert reset.reason == ""
    assert StreamReset(reason="provider retry").reason == "provider retry"
    assert is_event(reset)
    with pytest.raises(TypeError, match=r"StreamReset.reason must be a str"):
        StreamReset(reason=None)  # type: ignore[arg-type]


def test_tool_start_and_end_require_a_pairing_id() -> None:
    start = ToolStart(tool_use_id="t1", name="read", arguments={"path": "a"})
    assert start.arguments["path"] == "a"
    assert ToolStart(tool_use_id="t1", name="read").arguments == {}
    with pytest.raises(ValueError, match=r"ToolStart.tool_use_id is required"):
        ToolStart(tool_use_id="", name="read")

    end = ToolEnd(tool_use_id="t1", name="read", result=ToolResult(ok=True))
    assert end.result.ok
    with pytest.raises(ValueError, match=r"ToolEnd.tool_use_id is required"):
        ToolEnd(tool_use_id="", name="read", result=ToolResult(ok=True))
    with pytest.raises(TypeError, match=r"must be a ToolResult"):
        ToolEnd(tool_use_id="t1", name="read", result="ok")  # type: ignore[arg-type]


def test_approval_request_must_show_what_it_asks_about() -> None:
    request = ApprovalRequest(
        tool_use_id="t1",
        name="run_command",
        danger_level=DangerLevel.DESTRUCTIVE,
        rendered="rm -rf build/",
        reason="destructive",
    )
    assert request.rendered == "rm -rf build/"

    with pytest.raises(ValueError, match=r"tool_use_id is required"):
        ApprovalRequest(tool_use_id="", name="x", danger_level=DangerLevel.MUTATING, rendered="y")
    with pytest.raises(ValueError, match=r"cannot approve what is not shown"):
        ApprovalRequest(tool_use_id="t1", name="x", danger_level=DangerLevel.MUTATING, rendered="")
    with pytest.raises(TypeError, match=r"must be a DangerLevel"):
        ApprovalRequest(tool_use_id="t1", name="x", danger_level=1, rendered="y")  # type: ignore[arg-type]


def test_approval_decision_is_the_only_value_sent_back_into_the_loop() -> None:
    allow = ApprovalDecision(approved=True)
    assert (allow.reason, allow.remember) == ("", False)

    deny = ApprovalDecision(approved=False, reason="too risky")
    assert deny.approved is False and deny.reason == "too risky"

    # `remember` is a request to persist a rule, legal for both verdicts —
    # policy, not this type, decides whether the rule may be stored.
    assert ApprovalDecision(approved=True, remember=True).remember is True
    assert ApprovalDecision(approved=False, remember=True).remember is True

    with pytest.raises(TypeError, match=r"reason must be a str"):
        ApprovalDecision(approved=False, reason=None)  # type: ignore[arg-type]


def test_an_unattended_consumer_denies_rather_than_auto_allowing() -> None:
    assert DENY_UNATTENDED.approved is False
    assert DENY_UNATTENDED.reason.count("no human") == 1
    # It is a decision, not an event — it travels the other way.
    assert not is_event(DENY_UNATTENDED)


@pytest.mark.parametrize("state", [TurnState.DONE, TurnState.ERROR, TurnState.INTERRUPTED])
def test_turn_end_only_reports_a_terminal_state(state: TurnState) -> None:
    assert TurnEnd(turn_index=1, state=state).state is state


def test_turn_end_rejects_mid_turn_states_and_bad_indexes() -> None:
    with pytest.raises(ValueError, match=r"TurnEnd.state must be one of"):
        TurnEnd(turn_index=0, state=TurnState.THINKING)
    with pytest.raises(ValueError, match=r"turn_index must be >= 0"):
        TurnEnd(turn_index=-1, state=TurnState.DONE)


def test_error_requires_a_message_and_defaults_to_unrecoverable() -> None:
    error = Error(message="boom")
    assert (error.kind, error.recoverable) == ("unknown", False)
    assert Error(message="429", kind="rate_limit", recoverable=True).recoverable
    with pytest.raises(ValueError, match=r"Error.message is required"):
        Error(message="")


def test_the_event_union_is_closed_and_complete() -> None:
    samples = [
        TurnStart(turn_index=0),
        TextDelta("hi"),
        StreamReset(reason="retry"),
        ToolStart(tool_use_id="t1", name="read"),
        ToolOutput(tool_use_id="t1", chunk="line one\n"),
        ToolEnd(tool_use_id="t1", name="read", result=ToolResult(ok=True)),
        ApprovalRequest(
            tool_use_id="t1", name="rm", danger_level=DangerLevel.DESTRUCTIVE, rendered="rm -rf ."
        ),
        Compaction(folded_messages=12, token_estimate_before=9000, token_estimate_after=2000),
        VerifyResult(ran=True, passed=True, checks_passed=3),
        TurnEnd(turn_index=0, state=TurnState.DONE),
        Error(message="boom"),
    ]
    assert len(samples) == len(EVENT_TYPES)
    assert {type(sample) for sample in samples} == set(EVENT_TYPES)
    assert all(is_event(sample) for sample in samples)


# --------------------------------------------------------------------------- #
# Compaction
# --------------------------------------------------------------------------- #


def test_compaction_carries_before_and_after_so_the_ratio_is_visible() -> None:
    event = Compaction(folded_messages=20, token_estimate_before=8000, token_estimate_after=1500)
    assert event.folded_messages == 20
    assert event.token_estimate_after < event.token_estimate_before
    assert not event.summarizer_failed


def test_a_no_op_compaction_is_legal() -> None:
    """Equal before/after is a real outcome — nothing was foldable."""
    assert Compaction(folded_messages=0, token_estimate_before=500, token_estimate_after=500)


@pytest.mark.parametrize(
    "build",
    [
        lambda: Compaction(folded_messages=-1),
        lambda: Compaction(folded_messages=0, token_estimate_before=-1),
        lambda: Compaction(folded_messages=0, token_estimate_after=-1),
    ],
    ids=["folded", "before", "after"],
)
def test_compaction_rejects_negative_counts(build: Callable[[], Compaction]) -> None:
    # A thunk rather than a kwargs dict: `Compaction(**dict[str, int])` cannot type
    # check under --strict, since the dataclass also takes str and bool fields.
    with pytest.raises(ValueError):
        build()


def test_compaction_that_grew_the_transcript_is_rejected() -> None:
    """A summarizer that made the transcript bigger is a bug, not a nuance."""
    with pytest.raises(ValueError, match="cannot exceed"):
        Compaction(folded_messages=3, token_estimate_before=100, token_estimate_after=101)


def test_compaction_records_a_failed_summarizer_without_pretending_it_worked() -> None:
    event = Compaction(folded_messages=5, summarizer_failed=True, reason="window")
    assert event.summarizer_failed
    assert event.reason == "window"


# --------------------------------------------------------------------------- #
# VerifyResult
# --------------------------------------------------------------------------- #


def test_verify_result_distinguishes_did_not_run_from_failed() -> None:
    """The distinction that matters: no test command is not a broken change."""
    skipped = VerifyResult(ran=False, summary="no test command for this language")
    failed = VerifyResult(ran=True, passed=False, checks_failed=2)
    assert not skipped.ran and not skipped.passed
    assert failed.ran and not failed.passed
    assert skipped != failed


def test_verify_result_that_did_not_run_cannot_carry_results() -> None:
    with pytest.raises(ValueError, match="cannot carry results"):
        VerifyResult(ran=False, checks_passed=1)


def test_verify_result_cannot_pass_with_failed_checks() -> None:
    with pytest.raises(ValueError, match="passed=True"):
        VerifyResult(ran=True, passed=True, checks_failed=1)


def test_verify_result_rejects_negative_counts() -> None:
    with pytest.raises(ValueError):
        VerifyResult(ran=True, checks_passed=-1)


def test_verify_result_records_repair_separately_from_passing() -> None:
    """`repaired` says the loop fixed it; `passed` says it is now correct."""
    event = VerifyResult(ran=True, passed=True, checks_passed=4, repaired=True)
    assert event.repaired and event.passed


def test_non_events_are_rejected() -> None:
    assert not is_event("TextDelta")
    assert not is_event(Message(role=Role.USER))
    assert not is_event(None)


def test_turn_end_rejects_a_non_agent_state() -> None:
    """The last uncovered validator branch: `agent_state` is a type, not a dict."""
    with pytest.raises(TypeError, match="must be an AgentState or None"):
        TurnEnd(turn_index=0, state=TurnState.DONE, agent_state={"messages": ()})  # type: ignore[arg-type]
