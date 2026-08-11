"""The codec's refusals, and the last branches of the reader and the exporters.

`codec.py` has a section header that reads *"a wrong type is a refusal, never a
coercion"*, and that is the whole safety argument for this layer. A transcript is the
one input Ronin trusts by default — it wrote it — so a decoder that coerces turns an
edited or truncated file into a plausible-looking wrong state: a budget that reads
`"0"` as spent, an `is_error` of `"false"` that is truthy, a `tool_use_id` of `None`
that stringifies to `"None"` and matches nothing. Each of those resumes *successfully*
into a session that is quietly not the one on disk.

So every reader is tested through a real record on the real decoder, not by calling the
private helper: the promise is about what happens when a file is decoded, and a helper
tested in isolation proves nothing about whether a decoder reaches for it.

Each refusal is paired with the value it must still accept. A decoder that rejected
every optional field would satisfy the negative half of this file and make the layer
unusable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from persistence_harness import two_turn_session

from ronin.core.types import Error, Message, Role, Text, TurnEnd, TurnState
from ronin.persistence.codec import (
    SCHEMA_VERSION,
    TYPE_KEY,
    VERSION_KEY,
    MalformedRecord,
    UnknownRecordType,
    decode_block,
    decode_event,
    decode_state,
    encode_event,
)
from ronin.persistence.export import to_html, to_markdown
from ronin.persistence.resume import replay
from ronin.persistence.transcript import (
    HEADER_TAG,
    SessionMeta,
    TranscriptError,
    _read_meta,
    decode_meta,
    list_sessions,
    meta_path,
    read_events,
    session_path,
)


def record(tag: str, **fields: Any) -> dict[str, Any]:
    return {VERSION_KEY: SCHEMA_VERSION, TYPE_KEY: tag, **fields}


def event(tag: str, **fields: Any) -> Any:
    return decode_event(record(tag, **fields))


# --------------------------------------------------------------------------- #
# a required field that is absent
# --------------------------------------------------------------------------- #


def test_a_missing_required_field_names_the_record_and_the_field() -> None:
    """Both halves of "which record, which field" — a decoder that only said
    "malformed" would leave a maintainer diffing a JSONL file by eye."""
    with pytest.raises(MalformedRecord, match="turn_start") as caught:
        event("turn_start")
    assert "turn_index" in str(caught.value)


def test_a_required_field_present_is_read() -> None:
    assert event("turn_start", turn_index=4).turn_index == 4


# --------------------------------------------------------------------------- #
# optional fields: absent means the default, wrong type means a refusal
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("tag", "fields", "attribute", "expected"),
    [
        ("error", {"message": "m"}, "recoverable", False),
        ("error", {"message": "m"}, "kind", "unknown"),
        ("compaction", {"folded_messages": 3}, "token_estimate_before", 0),
        ("compaction", {"folded_messages": 3}, "summarizer_failed", False),
        ("verify_result", {}, "checks_passed", 0),
        ("stream_reset", {}, "reason", ""),
        ("tool_start", {"tool_use_id": "t", "name": "read"}, "arguments", {}),
    ],
    ids=["bool", "str", "int", "bool-2", "int-2", "str-2", "mapping"],
)
def test_an_absent_optional_field_takes_its_default(
    tag: str, fields: dict[str, Any], attribute: str, expected: object
) -> None:
    """Old transcripts are the reason these are optional at all: a field added in a
    later build is simply missing from every record written before it, and a schema
    bump for every new optional field would make every old session unreadable."""
    assert getattr(event(tag, **fields), attribute) == expected


@pytest.mark.parametrize(
    ("tag", "fields", "needle"),
    [
        ("error", {"message": "m", "recoverable": "yes"}, "must be a boolean"),
        ("error", {"message": "m", "recoverable": 1}, "must be a boolean"),
        (
            "compaction",
            {"folded_messages": 1, "token_estimate_before": "9400"},
            "must be an integer",
        ),
        ("compaction", {"folded_messages": 1, "token_estimate_before": True}, "must be an integer"),
        ("verify_result", {"checks_failed": 1.5}, "must be an integer"),
        (
            "tool_start",
            {"tool_use_id": "t", "name": "r", "arguments": "path=x"},
            "must be an object",
        ),
        ("tool_start", {"tool_use_id": "t", "name": "r", "arguments": [1, 2]}, "must be an object"),
        ("turn_start", {"turn_index": "3"}, "must be an integer"),
        ("error", {"message": 42}, "must be a string"),
    ],
    ids=[
        "bool-as-string",
        "bool-as-int",
        "int-as-string",
        "bool-as-int-field",
        "int-as-float",
        "mapping-as-string",
        "mapping-as-list",
        "required-int-as-string",
        "required-str-as-int",
    ],
)
def test_a_field_of_the_wrong_type_is_refused_rather_than_coerced(
    tag: str, fields: dict[str, Any], needle: str
) -> None:
    """`bool` before `int` in every check, deliberately: `True` *is* an `int` in Python,
    so a naive `isinstance(value, int)` accepts `"summarizer_failed": true` as a token
    count of 1 and the transcript decodes into numbers nobody wrote."""
    with pytest.raises(MalformedRecord, match=needle):
        event(tag, **fields)


def test_a_float_field_refuses_a_boolean_but_accepts_an_integer() -> None:
    """`spent_usd: 2` is a legitimate way for JSON to carry `2.0`; `spent_usd: true` is
    not a number at all, however much Python is willing to add it up."""
    state = decode_state(record("agent_state", budget={"spent_usd": 2}))
    assert state.budget.spent_usd == 2.0

    with pytest.raises(MalformedRecord, match="must be a number"):
        decode_state(record("agent_state", budget={"spent_usd": True}))


def test_a_mapping_with_a_non_string_key_is_refused() -> None:
    """JSON object keys are strings, so a non-string key means the record did not come
    from JSON — it came from a caller passing a Python dict straight in, and silently
    accepting that is how a key nobody can serialize reaches the encoder."""
    with pytest.raises(MalformedRecord, match="non-string key"):
        decode_event(
            {
                VERSION_KEY: SCHEMA_VERSION,
                TYPE_KEY: "tool_start",
                "tool_use_id": "t",
                "name": "read",
                "arguments": {1: "x"},
            }
        )


@pytest.mark.parametrize(
    ("value", "needle"),
    [("not-a-list", "must be an array"), (7, "must be an array"), ({"a": 1}, "must be an array")],
    ids=["string", "int", "mapping"],
)
def test_a_list_field_refuses_anything_that_is_not_an_array(value: Any, needle: str) -> None:
    """A string is a `Sequence` in Python, which is exactly the trap: iterating
    `"messages": "abc"` would yield three one-character "messages"."""
    with pytest.raises(MalformedRecord, match=needle):
        decode_state(record("agent_state", messages=value))


def test_an_absent_list_field_is_empty() -> None:
    assert decode_state(record("agent_state")).messages == ()


def test_a_list_of_the_wrong_element_type_names_the_index() -> None:
    """ "Which one" matters when a transcript has three hundred messages in it."""
    with pytest.raises(MalformedRecord, match=r"messages\[1\]"):
        decode_state(record("agent_state", messages=[{}, "not-an-object"]))


def test_an_unknown_danger_level_lists_the_ones_that_exist() -> None:
    """A danger level is stored by name so a reordered enum cannot silently downgrade a
    destructive tool. The cost is that an unknown name has to be refused — and the
    message has to say what the legal names are, since the reader is looking at a value
    that was legal in some other build."""
    with pytest.raises(MalformedRecord, match="not a DangerLevel name") as caught:
        event(
            "approval_request",
            tool_use_id="t",
            name="bash",
            danger_level="CATASTROPHIC",
            rendered="rm -rf /",
        )
    assert "DESTRUCTIVE" in str(caught.value)


def test_an_unknown_block_tag_is_refused_with_the_tags_that_exist() -> None:
    """Blocks have their own namespace, and a record that claims to be one and is not
    must not decode to an empty `Text` — a lost tool call changes what the model
    believes it did."""
    with pytest.raises(UnknownRecordType, match=re.escape("block.attachment")) as caught:
        decode_block(record("block.attachment", data="..."))
    assert "block.text" in str(caught.value)


# --------------------------------------------------------------------------- #
# transcript: the reader's remaining refusals
# --------------------------------------------------------------------------- #


def test_a_header_decoder_refuses_a_record_that_is_not_a_header() -> None:
    """The sidecar and the header line share one encoding, so `decode_meta` is handed
    whatever is in either place. An event record decoded as metadata would produce a
    session whose id came from `KeyError`."""
    with pytest.raises(TranscriptError, match=HEADER_TAG):
        decode_meta(record("text_delta", text="hi"))


@pytest.mark.parametrize(
    "line",
    ["[1, 2, 3]", '"just a string"', "42", "null", "true"],
    ids=["array", "string", "number", "null", "bool"],
)
def test_a_line_of_valid_json_that_is_not_an_object_is_not_a_record(
    tmp_path: Path, line: str
) -> None:
    """Every record is a JSON *object* — that is what carries the discriminator. A bare
    array parses fine and has no type, so it has to be refused explicitly rather than
    reaching a decoder that would ask it for `.get`."""
    path = tmp_path / "s.jsonl"
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(TranscriptError, match="not a JSON object"):
        read_events(path)


def test_a_sidecar_with_no_log_beside_it_describes_nothing(tmp_path: Path) -> None:
    """`None`, meaning "there is nothing here", as distinct from "there is something I
    cannot read" — which is the row the version lane returns instead.

    A sidecar whose log has been deleted is the leftover this reaches: the cache of a
    session that no longer exists. Called directly because `list_sessions` globs the
    *logs*, so it never asks about an id with no log — and that is exactly why the
    branch needs a test rather than being assumed unreachable.
    """
    meta_path(tmp_path, "orphan-sidecar").write_text("{not json", encoding="utf-8")
    assert _read_meta(tmp_path, "orphan-sidecar") is None
    assert list_sessions(tmp_path) == (), "a sidecar alone is not a session"


def test_a_log_of_only_a_newline_has_no_row(tmp_path: Path) -> None:
    """A file created and never written to. There is no identity to show, so the picker
    omits it rather than inventing a row with an id and nothing else."""
    (tmp_path / "blank.jsonl").write_text("\n", encoding="utf-8")
    assert list_sessions(tmp_path) == ()


def test_a_future_sidecar_beside_an_undecodable_header_reports_the_version(
    tmp_path: Path,
) -> None:
    """Both sources of truth are unusable, and they disagree about why.

    The sidecar says "another version" and the log's header is damaged in some other
    way. Reporting the version is the more useful of the two: it is actionable (another
    Ronin can read this), and a damaged header on a session whose sidecar is from
    another build is most likely damaged *because* it is from another build.
    """
    meta_path(tmp_path, "mixed").write_text(
        f'{{"{VERSION_KEY}": {SCHEMA_VERSION + 1}, "{TYPE_KEY}": "{HEADER_TAG}", '
        f'"session_id": "mixed"}}\n',
        encoding="utf-8",
    )
    # This build's version, so it is *not* the version lane that catches it: the header
    # has no `session_id`, which is a different failure, and the sidecar's version
    # problem is the more useful of the two to report.
    session_path(tmp_path, "mixed").write_text(
        f'{{"{VERSION_KEY}": {SCHEMA_VERSION}, "{TYPE_KEY}": "{HEADER_TAG}"}}\n',
        encoding="utf-8",
    )

    rows = list_sessions(tmp_path)
    assert [row.session_id for row in rows] == ["mixed"]
    assert "schema version" in rows[0].unreadable


# --------------------------------------------------------------------------- #
# resume: the mismatch that is a codec-bug canary
# --------------------------------------------------------------------------- #


def test_tool_use_ids_that_are_not_a_suffix_are_reported(tmp_path: Path) -> None:
    """The check exists because `TurnEnd.agent_state` *wins*, and a winner nobody audits
    is how a codec bug ships silently.

    Built by recording a checkpoint whose state omits the call the events contain — the
    shape a codec that dropped a `ToolUse` block would produce. The replay still
    succeeds (the recorded state is richer and is still the better base); it just says
    out loud that the two disagree.
    """
    events = list(two_turn_session())
    end = events[-1]
    assert isinstance(end, TurnEnd)
    stripped = (
        tuple(message for message in end.agent_state.messages if not message.tool_uses)
        if end.agent_state
        else ()
    )
    events[-1] = TurnEnd(
        turn_index=end.turn_index,
        state=end.state,
        stop_reason=end.stop_reason,
        agent_state=None
        if end.agent_state is None
        else end.agent_state.__class__(
            messages=stripped, cwd=end.agent_state.cwd, budget=end.agent_state.budget
        ),
    )

    result = replay(events)
    assert any("tool-use ids differ" in note for note in result.mismatches), result.mismatches
    assert result.state.pairing_errors == (), "a reported mismatch must still be sendable"


def test_a_faithful_recording_reports_no_mismatch() -> None:
    """The control. A canary that fires on every session is a canary nobody reads."""
    assert replay(list(two_turn_session())).mismatches == ()


# --------------------------------------------------------------------------- #
# export: an error inside a turn
# --------------------------------------------------------------------------- #


def error_session() -> tuple[Any, ...]:
    return (
        *two_turn_session(),
        Error(message="provider dropped the stream", kind="protocol", recoverable=True),
        Error(message="out of budget", kind="budget", recoverable=False),
        TurnEnd(turn_index=2, state=TurnState.ERROR, stop_reason="error"),
    )


def test_markdown_renders_an_error_with_its_kind_and_whether_it_was_fatal() -> None:
    """An export is what a user sends to a colleague to ask "what happened here", and
    the errors are usually the answer. Recoverable versus fatal is the distinction that
    tells them whether the run stopped there."""
    body = to_markdown(error_session(), SessionMeta(session_id="s1"))

    assert "**error (protocol, recoverable):** provider dropped the stream" in body
    assert "**error (budget, fatal):** out of budget" in body


def test_html_renders_the_same_errors(tmp_path: Path) -> None:
    """The two exporters must not disagree about what happened — one of them being
    silent about an error is worse than neither mentioning it."""
    body = to_html(error_session(), SessionMeta(session_id="s1"))
    assert "provider dropped the stream" in body
    assert "out of budget" in body


def test_an_error_is_attributed_to_the_turn_it_happened_in() -> None:
    """Errors are folded per turn, so one recorded after the last `TurnEnd` must not be
    silently dropped for having no turn to belong to."""
    events = (*two_turn_session(), Error(message="after the end", kind="tool"))
    assert "after the end" in to_markdown(events, SessionMeta(session_id="s1"))


def test_an_exported_error_message_is_escaped_in_html() -> None:
    """Error text is model- or tool-authored, so it is the same untrusted content as
    everything else in an export."""
    events = (*two_turn_session(), Error(message="<script>x</script>", kind="tool"))
    body = to_html(events, SessionMeta(session_id="s1"))
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;" in body


def test_a_message_with_no_blocks_still_encodes_and_decodes() -> None:
    """The degenerate value. An empty assistant message is what a stream that produced
    nothing leaves behind, and it must survive the round trip rather than become a
    `KeyError` on resume."""
    encoded = encode_event(TurnEnd(turn_index=0, state=TurnState.DONE, agent_state=None))
    decoded = decode_event(encoded)
    assert isinstance(decoded, TurnEnd)
    assert decoded.agent_state is None

    empty = Message(role=Role.ASSISTANT, content_blocks=())
    assert empty.content_blocks == ()
    assert Message(role=Role.USER, content_blocks=(Text(text=""),)).text == ""
