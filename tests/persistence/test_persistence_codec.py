"""The codec's job is exactness and loud refusal. These test both."""
from __future__ import annotations

import json
from typing import Any

import pytest
from persistence_harness import NESTED_ARGS, SIGNATURE, full_state, sample_event

from ronin.core.types import (
    EVENT_TYPES,
    AgentState,
    ApprovalRequest,
    Budget,
    DangerLevel,
    Error,
    Message,
    Mode,
    Role,
    Text,
    Thinking,
    ToolResult,
    ToolResultBlock,
    ToolUse,
    TurnEnd,
    TurnState,
)
from ronin.persistence import codec


def test_every_event_type_in_the_closed_union_has_a_codec_entry() -> None:
    """The anti-rot test: adding a member to ``Event`` without adding it here fails.

    Enumerates the union itself rather than a hand-written list, so the codec cannot
    silently stop covering it.
    """
    assert codec.missing_event_codecs() == ()
    assert set(codec.EVENT_TAGS) == set(EVENT_TYPES)
    assert len(set(codec.EVENT_TAGS.values())) == len(EVENT_TYPES), "duplicate tag"


@pytest.mark.parametrize("event_type", EVENT_TYPES, ids=lambda t: t.__name__)
def test_every_event_round_trips_through_json(event_type: type) -> None:
    """Through real ``json.dumps``/``loads``, not just dict equality — the file is
    the medium, and a value that only survives in memory has not been persisted."""
    event = sample_event(event_type)
    record = codec.encode_event(event)
    assert record[codec.TYPE_KEY] == codec.EVENT_TAGS[event_type]
    assert record[codec.VERSION_KEY] == codec.SCHEMA_VERSION
    restored = codec.decode_event(json.loads(json.dumps(record)))
    assert restored == event
    assert type(restored) is event_type


def test_a_thinking_signature_survives_byte_identical() -> None:
    """A mangled signature makes the provider reject the whole continuation, so
    newlines, tabs, non-ASCII and padding all have to come back untouched."""
    block = Thinking(text="reasoning", signature=SIGNATURE)
    restored = codec.decode_block(json.loads(json.dumps(codec.encode_block(block))))
    assert isinstance(restored, Thinking)
    assert restored.signature == SIGNATURE
    assert restored.signature.encode("utf-8") == SIGNATURE.encode("utf-8")


def test_tool_use_arguments_keep_their_nesting() -> None:
    use = ToolUse(id="t1", name="read", arguments=NESTED_ARGS)
    restored = codec.decode_block(json.loads(json.dumps(codec.encode_block(use))))
    assert isinstance(restored, ToolUse)
    assert restored.arguments == NESTED_ARGS
    assert restored.arguments["options"]["deep"]["flags"] == [1, 2.5, True, None, "x"]


@pytest.mark.parametrize("is_error", [True, False])
def test_the_is_error_flag_survives(is_error: bool) -> None:
    """``is_error`` is how the model tells a failure from an observation; losing it
    turns an error into a fact."""
    block = ToolResultBlock(tool_use_id="t1", content="boom", is_error=is_error)
    restored = codec.decode_block(json.loads(json.dumps(codec.encode_block(block))))
    assert isinstance(restored, ToolResultBlock)
    assert restored.is_error is is_error


@pytest.mark.parametrize(
    "value", [0.1234567, 1e-9, 12345.6789, 1 / 3, 2.0, 1e17]
)
def test_floats_round_trip_exactly(value: float) -> None:
    budget = Budget(max_usd=value, spent_usd=value, elapsed_seconds=value)
    restored = codec.decode_budget(json.loads(json.dumps(codec.encode_budget(budget))))
    assert restored == budget
    assert restored.spent_usd == value


def test_an_integral_float_stays_a_float() -> None:
    """JSON has one number type; ``spent_usd`` must not come back as ``int``."""
    restored = codec.decode_budget(
        json.loads(json.dumps(codec.encode_budget(Budget(spent_usd=1.0))))
    )
    assert isinstance(restored.spent_usd, float)


def test_a_whole_agent_state_round_trips_with_every_field_populated() -> None:
    state = full_state()
    restored = codec.decode_state(json.loads(json.dumps(codec.encode_state(state))))
    assert restored == state
    assert restored.pairing_errors == ()
    assert restored.mode is Mode.AUTO_EDIT
    assert restored.todos[1].subject == "fix it"


def test_none_and_zero_budget_ceilings_are_distinguishable() -> None:
    """``None`` means unbounded; a decoder that turns it into 0 would make every
    budget instantly exhausted."""
    restored = codec.decode_budget(
        json.loads(json.dumps(codec.encode_budget(Budget())))
    )
    assert restored.max_tokens is None and restored.max_usd is None
    assert restored.exhausted is False


def test_a_nested_agent_state_on_turn_end_round_trips() -> None:
    event = TurnEnd(
        turn_index=0,
        state=TurnState.DONE,
        stop_reason="no_tool_calls",
        agent_state=full_state(),
    )
    restored = codec.decode_event(json.loads(json.dumps(codec.encode_event(event))))
    assert restored == event


def test_generic_encode_and_decode_dispatch_on_the_tag() -> None:
    state = full_state()
    assert codec.decode(codec.encode(state)) == state
    event = Error(message="x")
    assert codec.decode(codec.encode(event)) == event


def test_a_danger_level_is_stored_by_name_not_by_rank() -> None:
    """The rank is a comparison order and exactly the kind of thing that gets
    renumbered; the name is stable."""
    event = ApprovalRequest(
        tool_use_id="t1",
        name="bash",
        danger_level=DangerLevel.IRREVERSIBLE,
        rendered="rm -rf /",
    )
    record = codec.encode_event(event)
    assert record["danger_level"] == "IRREVERSIBLE"
    assert codec.decode_event(record) == event


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


def test_another_schema_version_fails_naming_both_versions() -> None:
    record = codec.encode_event(Error(message="x"))
    record[codec.VERSION_KEY] = codec.SCHEMA_VERSION + 1
    with pytest.raises(codec.SchemaVersionMismatch) as caught:
        codec.decode_event(record)
    message = str(caught.value)
    assert str(codec.SCHEMA_VERSION + 1) in message
    assert str(codec.SCHEMA_VERSION) in message


def test_a_missing_schema_version_is_refused_too() -> None:
    record = codec.encode_event(Error(message="x"))
    del record[codec.VERSION_KEY]
    with pytest.raises(codec.SchemaVersionMismatch):
        codec.decode_event(record)


def test_an_unknown_discriminator_is_a_named_error_not_a_skipped_record() -> None:
    record = codec.encode_event(Error(message="x"))
    record[codec.TYPE_KEY] = "tool_screamed"
    with pytest.raises(codec.UnknownRecordType, match="tool_screamed"):
        codec.decode_event(record)


def test_a_record_with_no_discriminator_is_refused() -> None:
    with pytest.raises(codec.MalformedRecord):
        codec.decode_event({codec.VERSION_KEY: codec.SCHEMA_VERSION})


def test_an_event_type_outside_the_union_cannot_be_encoded() -> None:
    """Stands in for "core grew a member and this module did not"."""

    class Invented:
        pass

    with pytest.raises(codec.UnknownEventType, match="Invented"):
        codec.encode_event(Invented())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("record", "match"),
    [
        ({"text": 7}, "must be a string"),
        ({"text": None}, "must be a string"),
    ],
)
def test_a_wrong_field_type_is_refused_rather_than_coerced(
    record: dict[str, Any], match: str
) -> None:
    payload = {codec.VERSION_KEY: codec.SCHEMA_VERSION, codec.TYPE_KEY: "text_delta"}
    with pytest.raises(codec.MalformedRecord, match=match):
        codec.decode_event({**payload, **record})


def test_a_boolean_is_not_accepted_where_an_integer_belongs() -> None:
    """``bool`` is an ``int`` subclass; accepting it would turn ``True`` into 1."""
    payload = {
        codec.VERSION_KEY: codec.SCHEMA_VERSION,
        codec.TYPE_KEY: "turn_start",
        "turn_index": True,
    }
    with pytest.raises(codec.MalformedRecord, match="must be an integer"):
        codec.decode_event(payload)


def test_an_unknown_enum_value_names_the_known_ones() -> None:
    payload = {
        codec.VERSION_KEY: codec.SCHEMA_VERSION,
        codec.TYPE_KEY: "turn_end",
        "turn_index": 0,
        "state": "napping",
    }
    with pytest.raises(codec.MalformedRecord) as caught:
        codec.decode_event(payload)
    assert "napping" in str(caught.value) and "done" in str(caught.value)


def test_core_validation_still_applies_on_decode() -> None:
    """The codec does not build values behind the dataclasses' backs: a failed
    ``ToolResult`` with no error message is rejected on the way in, exactly as it
    is when a tool constructs one."""
    payload = codec.encode_tool_result(ToolResult(ok=False, error="why"))
    payload["error"] = ""
    with pytest.raises(ValueError, match="must say why"):
        codec.decode_tool_result(payload)


def test_a_tool_use_on_a_user_message_is_rejected_on_decode() -> None:
    record = codec.encode_message(
        Message(role=Role.ASSISTANT, content_blocks=(ToolUse(id="t", name="read"),))
    )
    record["role"] = Role.USER.value
    with pytest.raises(ValueError, match="only appear on an assistant"):
        codec.decode_message(record)


def test_an_agent_state_record_is_not_mistaken_for_an_event() -> None:
    with pytest.raises(codec.UnknownRecordType):
        codec.decode_event(codec.encode_state(AgentState()))
    with pytest.raises(codec.UnknownRecordType):
        codec.decode_state(codec.encode_event(Error(message="x")))


def test_block_tags_live_in_their_own_namespace() -> None:
    """A nested block record can never collide with a top-level event record."""
    assert all(tag.startswith("block.") for tag in codec.BLOCK_TAGS.values())
    assert not set(codec.BLOCK_TAGS.values()) & set(codec.EVENT_TAGS.values())


def test_a_text_block_round_trips_the_empty_string() -> None:
    restored = codec.decode_block(codec.encode_block(Text(text="")))
    assert restored == Text(text="")
