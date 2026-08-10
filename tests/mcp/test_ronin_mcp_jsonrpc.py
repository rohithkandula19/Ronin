"""The codec and the error-code table.

The table is the interesting half. A client that reads ``-32602`` as ``-32603``
retries a malformed call forever, so each code is asserted against the shape that
must produce it rather than against "an error happened".
"""

from __future__ import annotations

import json

import pytest

from ronin.mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    JSONRPC_VERSION,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SERVER_ERROR_RANGE,
    TOOL_EXECUTION_FAILED,
    JsonRpcError,
    ProtocolError,
    Request,
    Response,
    encode,
    is_retryable,
    parse_request,
    parse_response,
)


def test_a_request_without_an_id_is_a_notification() -> None:
    assert Request(method="notifications/initialized").is_notification
    assert not Request(method="ping", id=1).is_notification


def test_a_notification_carries_no_id_on_the_wire() -> None:
    """A null id is legal JSON-RPC and ambiguous with a notification, so we omit it."""
    wire = Request(method="notifications/initialized").to_wire()
    assert "id" not in wire
    assert wire == {"jsonrpc": JSONRPC_VERSION, "method": "notifications/initialized"}


def test_a_response_cannot_carry_both_a_result_and_an_error() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Response(id=1, result={}, error=JsonRpcError(INTERNAL_ERROR))


def test_a_response_must_carry_one_of_them() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Response(id=1)


def test_encoding_escapes_newlines_so_framing_stays_safe() -> None:
    """Newline-delimited framing depends on this; tool output is full of newlines."""
    payload = encode(Response(id=1, result={"text": "line one\nline two\r\nline three"}))
    assert payload.count(b"\n") == 1
    assert payload.endswith(b"\n")
    assert json.loads(payload)["result"]["text"] == "line one\nline two\r\nline three"


def test_encoding_survives_non_ascii_intact() -> None:
    payload = encode(Response(id="x", result={"text": "naïve — 日本語"}))
    assert json.loads(payload)["result"]["text"] == "naïve — 日本語"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("not json at all", PARSE_ERROR),
        ("", PARSE_ERROR),
        ("[1, 2, 3]", INVALID_REQUEST),
        ('"a string"', INVALID_REQUEST),
        ('{"method": "ping", "id": 1}', INVALID_REQUEST),
        ('{"jsonrpc": "1.0", "method": "ping", "id": 1}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "id": 1}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "method": "", "id": 1}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "method": "ping", "id": {"a": 1}}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "method": "ping", "id": true}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "method": "ping", "id": 1, "params": [1, 2]}', INVALID_PARAMS),
        ('{"jsonrpc": "2.0", "method": "ping", "id": 1, "params": "x"}', INVALID_PARAMS),
    ],
)
def test_the_parse_error_table(raw: str, code: int) -> None:
    with pytest.raises(JsonRpcError) as caught:
        parse_request(raw)
    assert caught.value.code == code


def test_a_batch_is_refused_rather_than_partially_answered() -> None:
    with pytest.raises(JsonRpcError) as caught:
        parse_request('[{"jsonrpc":"2.0","method":"ping","id":1}]')
    assert caught.value.code == INVALID_REQUEST


def test_absent_and_null_params_both_mean_no_params() -> None:
    assert parse_request('{"jsonrpc":"2.0","method":"ping","id":1}').params == {}
    assert parse_request('{"jsonrpc":"2.0","method":"ping","id":1,"params":null}').params == {}


def test_only_the_internal_error_is_worth_retrying() -> None:
    """The whole reason the codes are chosen carefully."""
    assert is_retryable(INTERNAL_ERROR)
    for code in (PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS):
        assert not is_retryable(code)
    assert not is_retryable(TOOL_EXECUTION_FAILED)


def test_the_server_defined_codes_sit_inside_the_reserved_range() -> None:
    """Outside it, a client is entitled to treat the code as meaningless."""
    assert TOOL_EXECUTION_FAILED in SERVER_ERROR_RANGE
    assert INTERNAL_ERROR not in SERVER_ERROR_RANGE


@pytest.mark.parametrize(
    "raw",
    [
        '{"jsonrpc":"2.0","id":1,"result":{},"error":{"code":-1,"message":"x"}}',
        '{"jsonrpc":"2.0","id":1}',
        '{"jsonrpc":"1.0","id":1,"result":{}}',
        '{"jsonrpc":"2.0","id":1,"result":[1,2]}',
        '{"jsonrpc":"2.0","id":1,"error":"boom"}',
        '{"jsonrpc":"2.0","id":1,"error":{"message":"no code"}}',
        '{"jsonrpc":"2.0","id":true,"result":{}}',
        "not json",
    ],
)
def test_a_malformed_response_raises_rather_than_replying(raw: str) -> None:
    """Client side: there is nobody to send an error code back to."""
    with pytest.raises((ProtocolError, JsonRpcError)):
        parse_response(raw)


def test_a_response_round_trips_through_the_codec() -> None:
    original = Response(id="abc", result={"tools": [{"name": "read"}]})
    assert parse_response(encode(original)) == original


def test_an_error_response_round_trips_with_its_data() -> None:
    original = Response(
        id=7, error=JsonRpcError(TOOL_EXECUTION_FAILED, "edit refused", {"tool": "edit"})
    )
    decoded = parse_response(encode(original))
    assert decoded.error is not None
    assert (decoded.error.code, decoded.error.message, decoded.error.data) == (
        TOOL_EXECUTION_FAILED,
        "edit refused",
        {"tool": "edit"},
    )


def test_an_error_with_no_message_still_reads_as_something() -> None:
    """A blank error string in a log is indistinguishable from no error at all."""
    assert JsonRpcError(METHOD_NOT_FOUND).to_wire()["message"] == "Method not found"
    assert "Method not found" in str(JsonRpcError(METHOD_NOT_FOUND))


def test_an_unknown_code_still_describes_itself() -> None:
    assert JsonRpcError(-32050).to_wire()["message"] == "Server error"


def test_a_request_rejects_a_boolean_id() -> None:
    """`True` is an int in Python, so the type checker allows it and it would
    silently become id=1 on the wire; only a runtime check catches it."""
    with pytest.raises(TypeError):
        Request(method="ping", id=True)
