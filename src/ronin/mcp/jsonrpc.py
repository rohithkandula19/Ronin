"""JSON-RPC 2.0, implemented over the standard library, for both sides of MCP.

MCP is JSON-RPC 2.0 plus a vocabulary of methods. The protocol is small enough
that an implementation is a few hundred lines and large enough that getting the
error codes wrong has consequences: a client that receives ``-32603`` (internal
error, "the server is broken, try again") where it should have received
``-32602`` (invalid params, "your request is wrong, fix it") will retry a
malformed call forever. So the codes are named constants here, the classifier is
a pure function, and the table is tested case by case.

Two shapes that are easy to conflate and are kept distinct:

* a **notification** is a request with no ``id`` and gets *no* response, ever —
  answering one is a protocol violation that some clients treat as a stray
  response and drop the connection over;
* a **response** carries exactly one of ``result`` or ``error``, never both and
  never neither, which is why :class:`Response` validates that rather than
  trusting the caller.

Framing is not here. A JSON-RPC message is a JSON object; how it is delimited on
a pipe or a socket is the transport's problem (see :mod:`ronin.mcp.transport`).
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: The only version string this implementation speaks. A message declaring
#: anything else is an invalid request, not a version to negotiate.
JSONRPC_VERSION = "2.0"

# --------------------------------------------------------------------------- #
# Error codes
# --------------------------------------------------------------------------- #

#: Reserved, pre-defined codes. These four are the whole spec-defined surface
#: plus the internal-error catch-all.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

#: ``-32000`` to ``-32099`` is the range JSON-RPC reserves for the server to
#: define. Ronin's server uses it for *application* failures — a tool that ran
#: and refused, or one whose approval gate said no. Deliberately outside the
#: reserved codes: a client must not treat "the tool said no" as "the transport
#: is broken", because the first is final and the second is worth a retry.
TOOL_EXECUTION_FAILED = -32000
APPROVAL_REQUIRED = -32002

#: Every code this module can emit, for the client's classifier and the tests.
SERVER_ERROR_RANGE = range(-32099, -31999)

ERROR_MESSAGES: Mapping[int, str] = {
    PARSE_ERROR: "Parse error",
    INVALID_REQUEST: "Invalid Request",
    METHOD_NOT_FOUND: "Method not found",
    INVALID_PARAMS: "Invalid params",
    INTERNAL_ERROR: "Internal error",
    TOOL_EXECUTION_FAILED: "Tool execution failed",
    APPROVAL_REQUIRED: "Approval required",
}


def is_retryable(code: int) -> bool:
    """Whether a client should consider re-sending the same request.

    Only ``-32603`` qualifies: it is the one code that means "the fault is mine,
    and it may be transient". Everything else is either the caller's fault
    (``-32600``/``-32601``/``-32602``/``-32700``) or an application answer that
    will be identical next time (the server-defined range). This predicate is the
    whole reason the codes are chosen carefully.
    """
    return code == INTERNAL_ERROR


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #

#: JSON-RPC allows a string, a number, or null for an id. Null is legal on the
#: wire but ambiguous with a notification, so this implementation never sends it.
MessageId = int | str


class McpError(Exception):
    """Base for everything this package raises internally.

    Nothing that crosses a *tool* boundary raises — those return
    ``ToolResult(ok=False)``. These exist for the layers below that boundary,
    where a failure has to travel up to the place that knows how to degrade.
    """


class ProtocolError(McpError):
    """A peer sent something that is not a well-formed message we can act on.

    Fatal for the connection it happened on: once a frame is unparseable there is
    no way to know where the next one starts, so the client marks the server dead
    and lets the reconnect path decide what happens next.
    """


class TransportClosed(McpError):
    """The peer went away — EOF, a killed subprocess, a dropped socket."""


@dataclass(frozen=True, slots=True)
class JsonRpcError(McpError):
    """An ``error`` object, usable both as a value and as a raise.

    A dataclass *and* an exception so the server can ``raise`` one deep in a
    handler and serialize the same object at the boundary, without a second
    parallel type that has to be kept in step.
    """

    code: int
    message: str = ""
    data: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, int) or isinstance(self.code, bool):
            raise TypeError("JsonRpcError.code must be an int")
        # Exception.__init__ is bypassed by the dataclass, and str(exc) is empty
        # without args — which turns a logged error into a blank line.
        Exception.__init__(self, f"{self.code}: {self.message or self.describe()}")

    def describe(self) -> str:
        return ERROR_MESSAGES.get(self.code, "Server error")

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"code": self.code, "message": self.message or self.describe()}
        if self.data is not None:
            wire["data"] = self.data
        return wire

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> JsonRpcError:
        code = payload.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise ProtocolError(f"error object has a non-integer code: {payload.get('code')!r}")
        message = payload.get("message")
        return cls(
            code=code,
            message=message if isinstance(message, str) else "",
            data=payload.get("data"),
        )


@dataclass(frozen=True, slots=True)
class Request:
    """One call. ``id is None`` means notification: no answer is permitted."""

    method: str
    params: Mapping[str, Any] = field(default_factory=dict)
    id: MessageId | None = None

    def __post_init__(self) -> None:
        if not self.method:
            raise ValueError("Request.method is required")
        if not isinstance(self.params, Mapping):
            raise TypeError("Request.params must be a mapping")
        if self.id is not None and not isinstance(self.id, (int, str)):
            raise TypeError("Request.id must be an int, a str, or None (notification)")
        if isinstance(self.id, bool):
            raise TypeError("Request.id must be an int, a str, or None (notification)")

    @property
    def is_notification(self) -> bool:
        return self.id is None

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": self.method}
        if self.id is not None:
            wire["id"] = self.id
        if self.params:
            wire["params"] = dict(self.params)
        return wire


@dataclass(frozen=True, slots=True)
class Response:
    """One answer. Exactly one of ``result`` / ``error`` is set."""

    id: MessageId | None
    result: Mapping[str, Any] | None = None
    error: JsonRpcError | None = None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.error is None):
            raise ValueError(
                "a Response carries exactly one of result or error — a client "
                "that receives both has no way to decide whether the call worked"
            )
        if self.result is not None and not isinstance(self.result, Mapping):
            raise TypeError("Response.result must be a mapping")

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": self.id}
        if self.error is not None:
            wire["error"] = self.error.to_wire()
        else:
            wire["result"] = dict(self.result or {})
        return wire


# --------------------------------------------------------------------------- #
# Codec
# --------------------------------------------------------------------------- #


def encode(message: Request | Response) -> bytes:
    """Serialize to one line of UTF-8, newline-terminated.

    ``json.dumps`` escapes every control character inside strings, so the encoded
    form is guaranteed to contain no interior newline — which is what makes
    newline-delimited framing safe for arbitrary tool output. That guarantee is
    load-bearing and is tested with content full of newlines.
    """
    text = json.dumps(message.to_wire(), separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8") + b"\n"


def decode_object(raw: bytes | str) -> Mapping[str, Any]:
    """Parse one frame into a JSON object, or raise :class:`JsonRpcError`.

    Raises with ``-32700`` for unparseable bytes and ``-32600`` for valid JSON
    that is not an object, because those are the two codes a peer needs in order
    to tell "you sent garbage" from "you sent the wrong shape".
    """
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise JsonRpcError(
            PARSE_ERROR, f"could not parse the frame as JSON: {exc}"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise JsonRpcError(
            INVALID_REQUEST,
            f"a JSON-RPC message must be an object, got {type(parsed).__name__}",
        )
    return parsed


def parse_request(raw: bytes | str) -> Request:
    """Decode an incoming request, raising :class:`JsonRpcError` with the code
    a well-behaved peer needs to fix its own bug.

    Batches (a top-level array) are rejected as invalid requests rather than
    silently taking the first element: MCP does not use them, and a partial
    answer to a batch is worse than a refusal.
    """
    payload = decode_object(raw)
    version = payload.get("jsonrpc")
    if version != JSONRPC_VERSION:
        raise JsonRpcError(
            INVALID_REQUEST,
            f"jsonrpc must be {JSONRPC_VERSION!r}, got {version!r}",
        )
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcError(
            INVALID_REQUEST, f"method must be a non-empty string, got {method!r}"
        )
    raw_id = payload.get("id")
    if raw_id is not None and (isinstance(raw_id, bool) or not isinstance(raw_id, (int, str))):
        raise JsonRpcError(
            INVALID_REQUEST, f"id must be a string, a number, or absent, got {raw_id!r}"
        )
    params = payload.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        # An array is legal JSON-RPC (positional params) but no MCP method uses
        # one, and accepting it would mean every handler guessing at position.
        raise JsonRpcError(
            INVALID_PARAMS,
            f"params must be an object; positional params are not used by MCP "
            f"(got {type(params).__name__})",
        )
    return Request(method=method, params=params, id=raw_id)


def parse_response(raw: bytes | str) -> Response:
    """Decode an incoming response. Raises :class:`ProtocolError` on nonsense.

    Client-side counterpart to :func:`parse_request`: there is nobody to send an
    error code back to, so a malformed response is an exception rather than a
    reply.
    """
    payload = decode_object(raw)
    if payload.get("jsonrpc") != JSONRPC_VERSION:
        raise ProtocolError(
            f"response declares jsonrpc={payload.get('jsonrpc')!r}, expected "
            f"{JSONRPC_VERSION!r}"
        )
    if "result" in payload and "error" in payload:
        raise ProtocolError("response carries both result and error")
    raw_id = payload.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str, type(None))):
        raise ProtocolError(f"response id must be a string, a number, or null, got {raw_id!r}")
    if "error" in payload:
        error = payload["error"]
        if not isinstance(error, Mapping):
            raise ProtocolError(f"response error must be an object, got {type(error).__name__}")
        return Response(id=raw_id, error=JsonRpcError.from_wire(error))
    if "result" not in payload:
        raise ProtocolError("response carries neither result nor error")
    result = payload["result"]
    if not isinstance(result, Mapping):
        raise ProtocolError(f"response result must be an object, got {type(result).__name__}")
    return Response(id=raw_id, result=result)


def error_response(id: MessageId | None, error: JsonRpcError) -> Response:
    """A failure answer. Present so no call site hand-builds the wire shape."""
    return Response(id=id, error=error)


__all__ = [
    "APPROVAL_REQUIRED",
    "ERROR_MESSAGES",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "JSONRPC_VERSION",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "SERVER_ERROR_RANGE",
    "TOOL_EXECUTION_FAILED",
    "JsonRpcError",
    "McpError",
    "MessageId",
    "ProtocolError",
    "Request",
    "Response",
    "TransportClosed",
    "decode_object",
    "encode",
    "error_response",
    "is_retryable",
    "parse_request",
    "parse_response",
]
