"""The wire format: every core value ⇄ a JSON-safe ``dict``, exactly once.

One rule governs this module: **a transcript either loads as what was written, or
it fails with a message naming both schema versions.** There is no third outcome.
A half-decoded transcript is worse than an unreadable one — it resumes a session
with a mangled ``Thinking.signature`` or a dropped ``is_error`` and the provider
rejects the continuation several turns later, far from the cause. So every record
carries :data:`SCHEMA_VERSION` and a type discriminator, and a version that is not
ours raises :class:`SchemaVersionMismatch` naming *found* and *supported* rather
than best-effort decoding the fields it recognises.

Why the version tags every record rather than only the session header: a session
written by one Ronin and appended to by the next would otherwise carry a header
that lies about its later lines. Per-record versioning costs a few bytes a line
and makes the file self-describing at any offset.

Two exactness constraints are load-bearing rather than tidy:

* ``Thinking.signature`` is provider-opaque. It is copied through as a string with
  no normalization, no stripping, no re-encoding — a mangled signature makes the
  provider reject the whole continuation.
* ``ToolUse.arguments`` keeps its nesting. Arguments arrive from the provider layer
  already JSON-shaped, so they pass through untouched; anything that is not
  JSON-serializable fails loudly at write time (see :mod:`ronin.persistence.transcript`)
  instead of being coerced into something the model never asked for.

The :data:`~ronin.core.types.Event` union is closed, which cuts both ways. On read,
an unknown discriminator is :class:`UnknownRecordType` — never a skipped record. On
write, an event type with no codec entry is :class:`UnknownEventType`, and
``tests/persistence/test_codec.py`` enumerates ``EVENT_TYPES`` against
:data:`EVENT_TAGS` so extending the union without extending the codec fails a test
rather than a session.

``DangerLevel`` is encoded by *name* while the ``StrEnum``\\ s are encoded by value:
``DangerLevel``'s value is a comparison rank, and a rank is exactly the kind of
thing that gets renumbered.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any

from ..core.types import (
    EVENT_TYPES,
    AgentState,
    ApprovalRequest,
    Budget,
    ContentBlock,
    DangerLevel,
    Error,
    Event,
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
    ToolResult,
    ToolResultBlock,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
    TurnState,
)

#: Bump on any change that an older reader would misread. Readers refuse anything
#: that is not exactly this; there is no forward-compatible subset.
SCHEMA_VERSION = 1

#: Record keys, short because they repeat on every line of every transcript.
VERSION_KEY = "v"
TYPE_KEY = "t"

#: Discriminator for a whole :class:`~ronin.core.types.AgentState` record.
STATE_TAG = "agent_state"


# --------------------------------------------------------------------------- #
# Errors — every failure names what it could not read
# --------------------------------------------------------------------------- #


class CodecError(ValueError):
    """Base for every decode/encode refusal. A ``ValueError`` because a bad record
    is bad data, not a broken program."""


class SchemaVersionMismatch(CodecError):
    """A record from a different schema version. Names both, and never decodes."""

    def __init__(self, found: object, supported: int = SCHEMA_VERSION) -> None:
        super().__init__(
            f"transcript record has schema version {found!r} but this Ronin reads "
            f"and writes version {supported}; refusing to partially decode it — "
            f"export the old session with the Ronin that wrote it, or start a new one"
        )
        self.found = found
        self.supported = supported


class UnknownRecordType(CodecError):
    """A discriminator this codec has no decoder for."""

    def __init__(self, tag: object, known: Sequence[str]) -> None:
        super().__init__(
            f"unknown record type {tag!r}; this codec knows {sorted(known)}"
        )
        self.tag = tag


class UnknownEventType(CodecError):
    """A Python type in the ``Event`` union with no codec entry.

    Reached only when ``ronin.core.types`` grows a member and this module does not,
    which the codec-coverage test exists to catch first.
    """

    def __init__(self, offending: type) -> None:
        super().__init__(
            f"no codec entry for event type {offending.__name__}; add it to "
            f"ronin.persistence.codec (the Event union is closed on purpose)"
        )
        self.offending = offending


class MalformedRecord(CodecError):
    """A record of a known type whose fields are missing or the wrong shape."""


# --------------------------------------------------------------------------- #
# Field readers — a wrong type is a refusal, never a coercion
# --------------------------------------------------------------------------- #


def _where(record: Mapping[str, Any], key: str) -> str:
    return f"{record.get(TYPE_KEY, '?')}.{key}"


def _require(record: Mapping[str, Any], key: str) -> Any:
    if key not in record:
        raise MalformedRecord(f"record {record.get(TYPE_KEY, '?')!r} is missing {key!r}")
    return record[key]


def _as_str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise MalformedRecord(f"{where} must be a string, got {type(value).__name__}")
    return value


def _req_str(record: Mapping[str, Any], key: str) -> str:
    return _as_str(_require(record, key), _where(record, key))


def _opt_str(record: Mapping[str, Any], key: str, default: str = "") -> str:
    if key not in record:
        return default
    return _as_str(record[key], _where(record, key))


def _req_int(record: Mapping[str, Any], key: str) -> int:
    value = _require(record, key)
    # bool is an int subclass; accepting it here would silently turn True into 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedRecord(
            f"{_where(record, key)} must be an integer, got {type(value).__name__}"
        )
    return value


def _opt_int(record: Mapping[str, Any], key: str, default: int = 0) -> int:
    if key not in record:
        return default
    return _req_int(record, key)


def _opt_bool(record: Mapping[str, Any], key: str, default: bool = False) -> bool:
    if key not in record:
        return default
    value = record[key]
    if not isinstance(value, bool):
        raise MalformedRecord(
            f"{_where(record, key)} must be a boolean, got {type(value).__name__}"
        )
    return value


def _opt_float(record: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    if key not in record:
        return default
    value = record[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MalformedRecord(
            f"{_where(record, key)} must be a number, got {type(value).__name__}"
        )
    return float(value)


def _opt_float_or_none(record: Mapping[str, Any], key: str) -> float | None:
    if record.get(key) is None:
        return None
    return _opt_float(record, key)


def _opt_int_or_none(record: Mapping[str, Any], key: str) -> int | None:
    if record.get(key) is None:
        return None
    return _req_int(record, key)


def _opt_mapping(record: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    if key not in record:
        return {}
    value = record[key]
    if not isinstance(value, Mapping):
        raise MalformedRecord(
            f"{_where(record, key)} must be an object, got {type(value).__name__}"
        )
    for name in value:
        if not isinstance(name, str):
            raise MalformedRecord(f"{_where(record, key)} has a non-string key {name!r}")
    return dict(value)


def _opt_list(record: Mapping[str, Any], key: str) -> Sequence[Any]:
    if key not in record:
        return ()
    value = record[key]
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise MalformedRecord(
            f"{_where(record, key)} must be an array, got {type(value).__name__}"
        )
    return value


def _str_tuple(record: Mapping[str, Any], key: str) -> tuple[str, ...]:
    return tuple(
        _as_str(item, f"{_where(record, key)}[{i}]")
        for i, item in enumerate(_opt_list(record, key))
    )


def _record_list(record: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    items: list[Mapping[str, Any]] = []
    for i, item in enumerate(_opt_list(record, key)):
        if not isinstance(item, Mapping):
            raise MalformedRecord(
                f"{_where(record, key)}[{i}] must be an object, got {type(item).__name__}"
            )
        items.append(item)
    return tuple(items)


def _enum(value: str, enum: type[Role] | type[Mode] | type[TurnState] | type[TodoStatus],
          where: str) -> Any:
    try:
        return enum(value)
    except ValueError as exc:
        raise MalformedRecord(
            f"{where} is {value!r}, which is not a {enum.__name__} "
            f"(known: {sorted(member.value for member in enum)})"
        ) from exc


def _danger(value: str, where: str) -> DangerLevel:
    try:
        return DangerLevel[value]
    except KeyError as exc:
        raise MalformedRecord(
            f"{where} is {value!r}, which is not a DangerLevel name "
            f"(known: {[member.name for member in DangerLevel]})"
        ) from exc


def check_version(record: Mapping[str, Any]) -> None:
    """Raise :class:`SchemaVersionMismatch` unless ``record`` is our version."""
    found = record.get(VERSION_KEY)
    if found != SCHEMA_VERSION:
        raise SchemaVersionMismatch(found)


def record_type(record: Mapping[str, Any]) -> str:
    """The discriminator of ``record``, or a refusal if it carries none."""
    tag = record.get(TYPE_KEY)
    if not isinstance(tag, str) or not tag:
        raise MalformedRecord(f"record has no {TYPE_KEY!r} discriminator: {record!r}")
    return tag


# --------------------------------------------------------------------------- #
# Content blocks — tagged in their own namespace so a nested value can never be
# mistaken for a top-level event record
# --------------------------------------------------------------------------- #

BLOCK_TAGS: Mapping[type, str] = MappingProxyType(
    {
        Text: "block.text",
        Thinking: "block.thinking",
        ToolUse: "block.tool_use",
        ToolResultBlock: "block.tool_result",
    }
)


def encode_block(block: ContentBlock) -> dict[str, Any]:
    """One content block as a tagged dict. Blocks are nested, so no version key."""
    if isinstance(block, Text):
        return {TYPE_KEY: BLOCK_TAGS[Text], "text": block.text}
    if isinstance(block, Thinking):
        # signature is copied verbatim: it is provider-opaque bytes-as-text.
        return {
            TYPE_KEY: BLOCK_TAGS[Thinking],
            "text": block.text,
            "signature": block.signature,
        }
    if isinstance(block, ToolUse):
        return {
            TYPE_KEY: BLOCK_TAGS[ToolUse],
            "id": block.id,
            "name": block.name,
            "arguments": dict(block.arguments),
        }
    return {
        TYPE_KEY: BLOCK_TAGS[ToolResultBlock],
        "tool_use_id": block.tool_use_id,
        "content": block.content,
        "is_error": block.is_error,
    }


def decode_block(record: Mapping[str, Any]) -> ContentBlock:
    """Rebuild one content block, or refuse."""
    tag = record_type(record)
    if tag == BLOCK_TAGS[Text]:
        return Text(text=_req_str(record, "text"))
    if tag == BLOCK_TAGS[Thinking]:
        return Thinking(
            text=_req_str(record, "text"), signature=_opt_str(record, "signature")
        )
    if tag == BLOCK_TAGS[ToolUse]:
        return ToolUse(
            id=_req_str(record, "id"),
            name=_req_str(record, "name"),
            arguments=_opt_mapping(record, "arguments"),
        )
    if tag == BLOCK_TAGS[ToolResultBlock]:
        return ToolResultBlock(
            tool_use_id=_req_str(record, "tool_use_id"),
            content=_opt_str(record, "content"),
            is_error=_opt_bool(record, "is_error"),
        )
    raise UnknownRecordType(tag, tuple(BLOCK_TAGS.values()))


# --------------------------------------------------------------------------- #
# Messages, todos, budget, tool results
# --------------------------------------------------------------------------- #


def encode_message(message: Message) -> dict[str, Any]:
    return {
        "role": message.role.value,
        "blocks": [encode_block(block) for block in message.content_blocks],
        "metadata": dict(message.metadata),
    }


def decode_message(record: Mapping[str, Any]) -> Message:
    role: Role = _enum(_req_str(record, "role"), Role, "message.role")
    return Message(
        role=role,
        content_blocks=tuple(
            decode_block(block) for block in _record_list(record, "blocks")
        ),
        metadata=_opt_mapping(record, "metadata"),
    )


def encode_tool_result(result: ToolResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "content": result.content,
        "error": result.error,
        "artifacts": list(result.artifacts),
        "tokens_estimate": result.tokens_estimate,
    }


def decode_tool_result(record: Mapping[str, Any]) -> ToolResult:
    return ToolResult(
        ok=_opt_bool(record, "ok"),
        content=_opt_str(record, "content"),
        error=_opt_str(record, "error"),
        artifacts=_str_tuple(record, "artifacts"),
        tokens_estimate=_opt_int(record, "tokens_estimate"),
    )


def encode_todo(todo: Todo) -> dict[str, Any]:
    return {"id": todo.id, "subject": todo.subject, "status": todo.status.value}


def decode_todo(record: Mapping[str, Any]) -> Todo:
    status: TodoStatus = _enum(
        _opt_str(record, "status", TodoStatus.PENDING.value), TodoStatus, "todo.status"
    )
    return Todo(
        id=_req_str(record, "id"), subject=_req_str(record, "subject"), status=status
    )


def encode_budget(budget: Budget) -> dict[str, Any]:
    return {
        "max_tokens": budget.max_tokens,
        "max_usd": budget.max_usd,
        "max_wall_seconds": budget.max_wall_seconds,
        "spent_tokens": budget.spent_tokens,
        "spent_usd": budget.spent_usd,
        "elapsed_seconds": budget.elapsed_seconds,
    }


def decode_budget(record: Mapping[str, Any]) -> Budget:
    return Budget(
        max_tokens=_opt_int_or_none(record, "max_tokens"),
        max_usd=_opt_float_or_none(record, "max_usd"),
        max_wall_seconds=_opt_float_or_none(record, "max_wall_seconds"),
        spent_tokens=_opt_int(record, "spent_tokens"),
        spent_usd=_opt_float(record, "spent_usd"),
        elapsed_seconds=_opt_float(record, "elapsed_seconds"),
    )


# --------------------------------------------------------------------------- #
# AgentState
# --------------------------------------------------------------------------- #


def encode_state(state: AgentState) -> dict[str, Any]:
    """A whole resumable state as one versioned record."""
    return {
        VERSION_KEY: SCHEMA_VERSION,
        TYPE_KEY: STATE_TAG,
        "messages": [encode_message(message) for message in state.messages],
        "todos": [encode_todo(todo) for todo in state.todos],
        "cwd": state.cwd,
        "budget": encode_budget(state.budget),
        "mode": state.mode.value,
        "checkpoint_id": state.checkpoint_id,
    }


def decode_state(record: Mapping[str, Any]) -> AgentState:
    """Rebuild an :class:`~ronin.core.types.AgentState`, version-checked."""
    check_version(record)
    if record_type(record) != STATE_TAG:
        raise UnknownRecordType(record.get(TYPE_KEY), (STATE_TAG,))
    mode: Mode = _enum(_opt_str(record, "mode", Mode.ASK.value), Mode, "agent_state.mode")
    checkpoint = record.get("checkpoint_id")
    return AgentState(
        messages=tuple(
            decode_message(message) for message in _record_list(record, "messages")
        ),
        todos=tuple(decode_todo(todo) for todo in _record_list(record, "todos")),
        cwd=_opt_str(record, "cwd", "."),
        budget=decode_budget(_opt_mapping(record, "budget")),
        mode=mode,
        checkpoint_id=None if checkpoint is None else _req_str(record, "checkpoint_id"),
    )


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


def _enc_turn_start(event: TurnStart) -> dict[str, Any]:
    return {"turn_index": event.turn_index, "state": event.state.value}


def _dec_turn_start(record: Mapping[str, Any]) -> Event:
    state: TurnState = _enum(
        _opt_str(record, "state", TurnState.THINKING.value), TurnState, "turn_start.state"
    )
    return TurnStart(turn_index=_req_int(record, "turn_index"), state=state)


def _enc_text_delta(event: TextDelta) -> dict[str, Any]:
    return {"text": event.text, "thinking": event.thinking}


def _dec_text_delta(record: Mapping[str, Any]) -> Event:
    return TextDelta(
        text=_req_str(record, "text"), thinking=_opt_bool(record, "thinking")
    )


def _enc_stream_reset(event: StreamReset) -> dict[str, Any]:
    return {"reason": event.reason}


def _dec_stream_reset(record: Mapping[str, Any]) -> Event:
    return StreamReset(reason=_opt_str(record, "reason"))


def _enc_tool_start(event: ToolStart) -> dict[str, Any]:
    return {
        "tool_use_id": event.tool_use_id,
        "name": event.name,
        "arguments": dict(event.arguments),
    }


def _dec_tool_start(record: Mapping[str, Any]) -> Event:
    return ToolStart(
        tool_use_id=_req_str(record, "tool_use_id"),
        name=_req_str(record, "name"),
        arguments=_opt_mapping(record, "arguments"),
    )


def _enc_tool_end(event: ToolEnd) -> dict[str, Any]:
    return {
        "tool_use_id": event.tool_use_id,
        "name": event.name,
        "result": encode_tool_result(event.result),
    }


def _dec_tool_end(record: Mapping[str, Any]) -> Event:
    return ToolEnd(
        tool_use_id=_req_str(record, "tool_use_id"),
        name=_req_str(record, "name"),
        result=decode_tool_result(_opt_mapping(record, "result")),
    )


def _enc_approval_request(event: ApprovalRequest) -> dict[str, Any]:
    return {
        "tool_use_id": event.tool_use_id,
        "name": event.name,
        "danger_level": event.danger_level.name,
        "rendered": event.rendered,
        "reason": event.reason,
    }


def _dec_approval_request(record: Mapping[str, Any]) -> Event:
    return ApprovalRequest(
        tool_use_id=_req_str(record, "tool_use_id"),
        name=_req_str(record, "name"),
        danger_level=_danger(
            _req_str(record, "danger_level"), "approval_request.danger_level"
        ),
        rendered=_req_str(record, "rendered"),
        reason=_opt_str(record, "reason"),
    )


def _enc_turn_end(event: TurnEnd) -> dict[str, Any]:
    return {
        "turn_index": event.turn_index,
        "state": event.state.value,
        "stop_reason": event.stop_reason,
        "agent_state": (
            None if event.agent_state is None else encode_state(event.agent_state)
        ),
    }


def _dec_turn_end(record: Mapping[str, Any]) -> Event:
    state: TurnState = _enum(_req_str(record, "state"), TurnState, "turn_end.state")
    nested = record.get("agent_state")
    return TurnEnd(
        turn_index=_req_int(record, "turn_index"),
        state=state,
        stop_reason=_opt_str(record, "stop_reason"),
        agent_state=None if nested is None else decode_state(_opt_mapping(record, "agent_state")),
    )


def _enc_error(event: Error) -> dict[str, Any]:
    return {
        "message": event.message,
        "kind": event.kind,
        "recoverable": event.recoverable,
    }


def _dec_error(record: Mapping[str, Any]) -> Event:
    return Error(
        message=_req_str(record, "message"),
        kind=_opt_str(record, "kind", "unknown"),
        recoverable=_opt_bool(record, "recoverable"),
    )


#: type → (discriminator, encoder). The single table both directions derive from,
#: so a member that is missing here is missing everywhere and visibly so.
_EVENT_CODECS: Mapping[type, tuple[str, Callable[[Any], dict[str, Any]]]] = MappingProxyType(
    {
        TurnStart: ("turn_start", _enc_turn_start),
        TextDelta: ("text_delta", _enc_text_delta),
        StreamReset: ("stream_reset", _enc_stream_reset),
        ToolStart: ("tool_start", _enc_tool_start),
        ToolEnd: ("tool_end", _enc_tool_end),
        ApprovalRequest: ("approval_request", _enc_approval_request),
        TurnEnd: ("turn_end", _enc_turn_end),
        Error: ("error", _enc_error),
    }
)

#: event type → discriminator. Enumerated against ``core.types.EVENT_TYPES`` by
#: the codec-coverage test, which is what stops this table from rotting.
EVENT_TAGS: Mapping[type, str] = MappingProxyType(
    {event_type: tag for event_type, (tag, _) in _EVENT_CODECS.items()}
)

_EVENT_DECODERS: Mapping[str, Callable[[Mapping[str, Any]], Event]] = MappingProxyType(
    {
        "turn_start": _dec_turn_start,
        "text_delta": _dec_text_delta,
        "stream_reset": _dec_stream_reset,
        "tool_start": _dec_tool_start,
        "tool_end": _dec_tool_end,
        "approval_request": _dec_approval_request,
        "turn_end": _dec_turn_end,
        "error": _dec_error,
    }
)


def encode_event(event: Event) -> dict[str, Any]:
    """One event as a versioned, discriminated record.

    Dispatches on ``type(event)`` through a table rather than an ``isinstance``
    chain: the table is the thing the coverage test can enumerate.
    """
    entry = _EVENT_CODECS.get(type(event))
    if entry is None:
        raise UnknownEventType(type(event))
    tag, encoder = entry
    return {VERSION_KEY: SCHEMA_VERSION, TYPE_KEY: tag, **encoder(event)}


def decode_event(record: Mapping[str, Any]) -> Event:
    """Rebuild one event, version-checked, or refuse by name."""
    check_version(record)
    tag = record_type(record)
    decoder = _EVENT_DECODERS.get(tag)
    if decoder is None:
        raise UnknownRecordType(tag, tuple(_EVENT_DECODERS))
    return decoder(record)


def encode(value: Event | AgentState) -> dict[str, Any]:
    """Encode either an event or a whole state; the tag says which it was."""
    if isinstance(value, AgentState):
        return encode_state(value)
    return encode_event(value)


def decode(record: Mapping[str, Any]) -> Event | AgentState:
    """Inverse of :func:`encode`, dispatching on the record's discriminator."""
    check_version(record)
    if record_type(record) == STATE_TAG:
        return decode_state(record)
    return decode_event(record)


def missing_event_codecs() -> tuple[str, ...]:
    """Names of ``EVENT_TYPES`` members this codec cannot write. Empty is correct.

    Exposed rather than kept in the test so a caller can assert it at startup if it
    ever links against a core built from a different revision.
    """
    return tuple(
        event_type.__name__ for event_type in EVENT_TYPES if event_type not in EVENT_TAGS
    )


__all__ = [
    "BLOCK_TAGS",
    "EVENT_TAGS",
    "SCHEMA_VERSION",
    "STATE_TAG",
    "TYPE_KEY",
    "VERSION_KEY",
    "CodecError",
    "MalformedRecord",
    "SchemaVersionMismatch",
    "UnknownEventType",
    "UnknownRecordType",
    "check_version",
    "decode",
    "decode_block",
    "decode_budget",
    "decode_event",
    "decode_message",
    "decode_state",
    "decode_todo",
    "decode_tool_result",
    "encode",
    "encode_block",
    "encode_budget",
    "encode_event",
    "encode_message",
    "encode_state",
    "encode_todo",
    "encode_tool_result",
    "missing_event_codecs",
    "record_type",
]
