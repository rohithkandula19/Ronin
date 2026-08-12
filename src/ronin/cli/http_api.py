"""``ronin`` behind an OpenAI- and Anthropic-shaped HTTP API — the loop, wearing a wire.

What this exposes is not a model but the *whole agent*: a ``POST /v1/chat/completions``
opens a :class:`~ronin.cli.sdk.Agent`, runs Ronin's full agentic loop over the request's
last user message, and returns the answer folded into the exact JSON shape the OpenAI
SDK expects — so any tool that already speaks the OpenAI (or Anthropic) wire format can
point its ``base_url`` at Ronin and get an agent instead of a completion. It lives in
``cli/`` for the same reason ``cli/serve.py`` does: it drives ``cli.sdk.Agent``, which is
the CLI's job and not the core's.

**Stdlib, not a framework.** The server is :class:`http.server.ThreadingHTTPServer` and
``json`` — no fastapi/flask/uvicorn — because this tree ships **zero hard dependencies**
(see ``pyproject.toml``) and a web microframework to answer three routes would be the
first one.

**The wire is separated from the loop, and the loop half is a pure function.**
:func:`run_openai_chat` and :func:`run_anthropic_messages` take an already-parsed body
and an injected :data:`AgentFactory`, and return a response *dict* — no socket, no
:mod:`asyncio` runner, no ``self``. Everything that can go wrong with a request is
therefore testable by calling a function, and :func:`route` folds parsing, dispatch and
error-shaping into one more pure (async) function so the HTTP handler is a dozen lines
that only move bytes. The handler runs each request's coroutine with ``asyncio.run`` on
its own thread, which is the simplest thing that works: every request is an independent
session.

**Three decisions worth stating, because a caller will hit all three:**

* **One request is one fresh session.** These endpoints are stateless the way the real
  APIs are — the client resends the whole ``messages`` array every call. Ronin does not
  replay that transcript; it takes the **last user message** as the prompt and runs its
  own loop, with its own memory, repo map and tools. Prior turns and ``system`` messages
  in the array are context the *client* manages, not Ronin's conversation.
* **Streaming (SSE) is a documented non-goal for v1.** ``"stream": true`` is ignored and
  a single, whole completion is returned. The loop is folded to one ``AgentResult``;
  re-emitting its event stream as ``text/event-stream`` deltas is a later change.
* **Errors are responses, never crashes.** A malformed body is a ``400`` carrying the
  provider's own error shape; a loop that *raises* is a ``500`` with a shaped error; a
  loop that *completes unhappily* (an approval was refused, say) is still a ``200`` whose
  content is whatever the loop produced — hiding that behind a 5xx would throw away the
  model's own explanation. The handler never lets a traceback reach the client.

Token usage is reported honestly-but-coarsely: :class:`~ronin.cli.sdk.AgentResult`
exposes one spend total, not the prompt/completion (input/output) split the wire formats
distinguish, so the whole count is reported on the *output* side (``completion_tokens`` /
``output_tokens``) — which keeps ``prompt + completion == total`` true for OpenAI — and
the input side is ``0`` rather than a fabricated division.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .sdk import Agent, AgentResult

#: A zero-argument factory that opens a fresh :class:`~ronin.cli.sdk.Agent`. Injected
#: rather than built here so the core functions never decide a workspace, a mode, a
#: router or whether MCP is connected — the CLI subcommand (or a test) owns all of that
#: and hands in the one thing these functions need: something that yields an agent.
AgentFactory = Callable[[], Awaitable[Agent]]

#: The three routes served. Named so the banner, the router and the tests cannot drift.
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
MESSAGES_PATH = "/v1/messages"
MODELS_PATH = "/v1/models"

#: What ``GET /v1/models`` advertises, and the ``model`` echoed back when a request names
#: none. A single honest id — inventing a menu of model names Ronin does not have would
#: mislead a client's model picker.
DEFAULT_MODEL = "ronin"


class BadRequest(Exception):
    """A request body Ronin cannot turn into a prompt.

    Raised by the extraction/validation helpers and caught by :func:`route`, which turns
    it into a ``400`` carrying the shaped error for the endpoint that was called. It is a
    *value* error about the request, never about the loop — a loop failure is a different
    path (a ``500``), because the two mean different things to the caller.
    """


# --------------------------------------------------------------------------- #
# Message extraction — the one place the two wire formats agree
# --------------------------------------------------------------------------- #


def flatten_content(content: Any) -> str:
    """The text of one message's ``content``, whether a string or a block list.

    Both formats let ``content`` be a bare string *or* a list of typed blocks
    (``{"type": "text", "text": ...}`` and, in real traffic, image/other blocks Ronin has
    no text for). Text blocks are joined with newlines and everything else is dropped; a
    ``content`` that is neither a string nor a list, or a list holding a non-object block,
    is malformed and raises :class:`BadRequest` rather than being silently coerced.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
            if not isinstance(block, Mapping):
                raise BadRequest("each item in a content array must be an object")
            if block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str) and text:
                    texts.append(text)
        return "\n".join(texts)
    raise BadRequest("message 'content' must be a string or an array of content blocks")


def last_user_text(body: Mapping[str, Any]) -> str:
    """The prompt: the text of the **last** ``user`` message in ``body["messages"]``.

    The last one, not the concatenation of all of them, because the client resends the
    whole transcript each call and Ronin runs its own loop from the newest request (see
    the module docstring). A missing or empty ``messages`` array, a non-object message, a
    transcript with no user turn, or a last user turn with no text are all
    :class:`BadRequest` — an empty prompt would start a loop with nothing to do.
    """
    messages = body.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, str) or not messages:
        raise BadRequest("'messages' is required and must be a non-empty array")
    for message in reversed(list(messages)):
        if not isinstance(message, Mapping):
            raise BadRequest("each message in 'messages' must be an object")
        if message.get("role") == "user":
            text = flatten_content(message.get("content", "")).strip()
            if not text:
                raise BadRequest("the last user message has no text content")
            return text
    raise BadRequest("no message with role 'user' found in 'messages'")


def _model_name(body: Mapping[str, Any]) -> str:
    """The ``model`` echoed back, defaulting to :data:`DEFAULT_MODEL`.

    Echoed rather than validated: a client that asked for ``"gpt-4o"`` gets ``"gpt-4o"``
    back, because refusing an unknown model id would break clients that hardcode one, and
    the honest answer to "which model ran?" is the same whatever the label — Ronin's loop.
    """
    model = body.get("model")
    if isinstance(model, str) and model:
        return model
    return DEFAULT_MODEL


# --------------------------------------------------------------------------- #
# Error shaping — each provider's own error envelope
# --------------------------------------------------------------------------- #


def openai_error(
    message: str, *, type_: str = "invalid_request_error", code: str | None = None
) -> dict[str, Any]:
    """An OpenAI-shaped error body: ``{"error": {message, type, param, code}}``."""
    return {"error": {"message": message, "type": type_, "param": None, "code": code}}


def anthropic_error(message: str, *, type_: str = "invalid_request_error") -> dict[str, Any]:
    """An Anthropic-shaped error body: ``{"type": "error", "error": {type, message}}``."""
    return {"type": "error", "error": {"type": type_, "message": message}}


# --------------------------------------------------------------------------- #
# The core: pure, async, socket-free
# --------------------------------------------------------------------------- #


async def _run_agent(open_agent: AgentFactory, prompt: str) -> AgentResult:
    """Open an agent, run one prompt to its folded result, and always close it.

    The factory owns opening; this owns the lifecycle. ``aclose`` runs in a ``finally``
    because an agent holds shells, MCP connections and a transcript, and one request that
    forgot to release them leaks all three per call.
    """
    agent = await open_agent()
    try:
        return await agent.run(prompt)
    finally:
        await agent.aclose()


def _output_tokens(result: AgentResult) -> int:
    """The one token number the folded result exposes (see the module docstring)."""
    return max(result.state.tokens, 0)


async def run_openai_chat(
    body: Mapping[str, Any],
    *,
    open_agent: AgentFactory,
    created: int = 0,
    seq: int = 0,
) -> dict[str, Any]:
    """One OpenAI ``chat/completions`` request, run as a whole agent turn.

    Extracts the prompt (:func:`last_user_text` — raises :class:`BadRequest` on a bad
    body), runs the loop via ``open_agent``, and returns the ``chat.completion`` object.
    ``created`` and ``seq`` are injected so the response is reproducible: ``seq`` names the
    completion (``chatcmpl-<seq>``) and ``created`` is the unix timestamp the caller
    supplies — nothing here reads the clock or a random source.

    ``finish_reason`` is always ``"stop"``: Ronin's tool calls happen *inside* the loop
    and are not surfaced as OpenAI ``tool_calls``, so from the caller's side the turn
    always ends normally with an answer.
    """
    prompt = last_user_text(body)
    result = await _run_agent(open_agent, prompt)
    total = _output_tokens(result)
    return {
        "id": f"chatcmpl-{seq}",
        "object": "chat.completion",
        "created": created,
        "model": _model_name(body),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": total,
            "total_tokens": total,
        },
    }


async def run_anthropic_messages(
    body: Mapping[str, Any],
    *,
    open_agent: AgentFactory,
    seq: int = 0,
) -> dict[str, Any]:
    """One Anthropic ``/v1/messages`` request, run as a whole agent turn.

    Same shape of work as :func:`run_openai_chat`, returning the ``message`` object.
    ``max_tokens`` is read off the wire for compatibility but not enforced: it caps the
    model's *output* tokens, whereas Ronin's loop manages a whole-session budget of its
    own, and mapping one onto the other would silently mean something different. ``seq``
    names the message (``msg_<seq>``); ``stop_reason`` is always ``"end_turn"`` for the
    reason ``finish_reason`` is always ``"stop"`` above.
    """
    prompt = last_user_text(body)
    result = await _run_agent(open_agent, prompt)
    return {
        "id": f"msg_{seq}",
        "type": "message",
        "role": "assistant",
        "model": _model_name(body),
        "content": [{"type": "text", "text": result.text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": _output_tokens(result)},
    }


def models_list(*, models: Sequence[str] = (DEFAULT_MODEL,)) -> dict[str, Any]:
    """The ``GET /v1/models`` body: the OpenAI list envelope over one honest id."""
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "created": 0, "owned_by": "ronin"} for name in models
        ],
    }


# --------------------------------------------------------------------------- #
# Routing — still pure, still async, still no socket
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Response:
    """An HTTP status and a JSON body, decided before a single byte is sent."""

    status: HTTPStatus
    body: dict[str, Any]


def _openai_shape(message: str, type_: str) -> dict[str, Any]:
    return openai_error(message, type_=type_)


def _anthropic_shape(message: str, type_: str) -> dict[str, Any]:
    return anthropic_error(message, type_=type_)


async def _run_json(
    raw_body: bytes,
    *,
    shape: Callable[[str, str], dict[str, Any]],
    run: Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]],
) -> Response:
    """Parse a JSON object body, run it, and shape every failure with ``shape``.

    The three outcomes a caller can cause are separated on purpose: unparseable or
    non-object JSON and a :class:`BadRequest` from the body are ``400`` (the client's
    mistake); anything the loop *raises* is a ``500`` (Ronin's) — surfaced as a message,
    never as a traceback.
    """
    try:
        parsed = json.loads(raw_body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return Response(
            HTTPStatus.BAD_REQUEST,
            shape(f"could not parse request body as JSON: {exc}", "invalid_request_error"),
        )
    if not isinstance(parsed, Mapping):
        return Response(
            HTTPStatus.BAD_REQUEST,
            shape("request body must be a JSON object", "invalid_request_error"),
        )
    try:
        body = await run(parsed)
    except BadRequest as exc:
        return Response(HTTPStatus.BAD_REQUEST, shape(str(exc), "invalid_request_error"))
    except Exception as exc:  # the boundary: a loop failure is a 500, never a crash
        return Response(
            HTTPStatus.INTERNAL_SERVER_ERROR, shape(f"agent run failed: {exc}", "api_error")
        )
    return Response(HTTPStatus.OK, body)


async def route(
    method: str,
    path: str,
    raw_body: bytes,
    *,
    open_agent: AgentFactory,
    created: int = 0,
    seq: int = 0,
) -> Response:
    """The whole HTTP contract as one function: ``(method, path, body) -> Response``.

    Socket-free and total — every path returns a :class:`Response`, so the handler that
    calls this only has to serialize one. An unknown route is a ``404`` shaped for the
    endpoint family the path looks like (Anthropic under ``/v1/messages``, OpenAI
    otherwise), so a client gets back an error in the envelope it knows how to read.
    """
    if method == "GET" and path == MODELS_PATH:
        return Response(HTTPStatus.OK, models_list())
    if method == "POST" and path == CHAT_COMPLETIONS_PATH:
        return await _run_json(
            raw_body,
            shape=_openai_shape,
            run=lambda body: run_openai_chat(body, open_agent=open_agent, created=created, seq=seq),
        )
    if method == "POST" and path == MESSAGES_PATH:
        return await _run_json(
            raw_body,
            shape=_anthropic_shape,
            run=lambda body: run_anthropic_messages(body, open_agent=open_agent, seq=seq),
        )
    shape = _anthropic_shape if path == MESSAGES_PATH else _openai_shape
    return Response(HTTPStatus.NOT_FOUND, shape(f"no route for {method} {path}", "not_found_error"))


# --------------------------------------------------------------------------- #
# The thin HTTP layer
# --------------------------------------------------------------------------- #


class RoninHTTPServer(ThreadingHTTPServer):
    """A :class:`~http.server.ThreadingHTTPServer` that carries the agent factory.

    The factory is stashed here because a :class:`~http.server.BaseHTTPRequestHandler` is
    instantiated per request with no other channel to reach it — the handler reads it off
    ``self.server``. The per-request id counter lives here too, behind a lock, because
    the threading server answers requests concurrently and two completions must not share
    an id (duplicate ids have caused real provider 400s elsewhere in this tree).
    """

    #: A stuck handler thread must not keep the process alive after ``shutdown``.
    daemon_threads = True
    #: Rebind the port immediately on restart rather than waiting out ``TIME_WAIT``.
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        open_agent: AgentFactory,
        log: Callable[[str], None] | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(address, handler)
        self.open_agent = open_agent
        self._log = log
        self._clock = clock if clock is not None else (lambda: int(time.time()))
        self._seq = 0
        self._seq_lock = threading.Lock()

    def next_seq(self) -> int:
        """The next completion id, unique across concurrent handler threads."""
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def created(self) -> int:
        """The ``created`` timestamp for a response, from the injected clock."""
        return self._clock()

    def note(self, message: str) -> None:
        """Route a line to the injected logger, or drop it if there is none."""
        if self._log is not None:
            self._log(message)


class RoninHTTPHandler(BaseHTTPRequestHandler):
    """Move bytes: read the body, hand it to :func:`route`, write the JSON back.

    Every decision has already been made by the time this runs — status and body come
    from a :class:`Response` — so the only failure this layer can add is one in the
    plumbing (reading the body, running the loop's event loop), which is caught and
    turned into a shaped ``500`` rather than a traceback on the wire.
    """

    #: HTTP/1.1 so keep-alive works; safe because every response sets Content-Length.
    protocol_version = "HTTP/1.1"
    #: A slow or half-open client cannot wedge a handler thread forever.
    timeout = 30

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def log_message(self, format: str, *args: Any) -> None:
        """Silence stdlib's stderr access log; forward to the server's logger if any."""
        server = self.server
        if isinstance(server, RoninHTTPServer):
            server.note((format % args) + "\n")

    def _handle(self, method: str) -> None:
        server = self.server
        assert isinstance(server, RoninHTTPServer), "handler wired to the wrong server"
        try:
            raw = self._read_body()
            path = urlsplit(self.path).path
            response = asyncio.run(
                route(
                    method,
                    path,
                    raw,
                    open_agent=server.open_agent,
                    created=server.created(),
                    seq=server.next_seq(),
                )
            )
        except Exception as exc:  # a plumbing failure is a 500, never a traceback on the wire
            response = Response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                openai_error(f"internal error: {exc}", type_="api_error"),
            )
        self._send(response)

    def _read_body(self) -> bytes:
        """Exactly the ``Content-Length`` bytes, or none. A bad length reads as none."""
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError:
            return b""
        return self.rfile.read(length) if length > 0 else b""

    def _send(self, response: Response) -> None:
        payload = json.dumps(response.body).encode("utf-8")
        self.send_response(int(response.status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def build_server(
    address: tuple[str, int],
    *,
    open_agent: AgentFactory,
    log: Callable[[str], None] | None = None,
    clock: Callable[[], int] | None = None,
) -> RoninHTTPServer:
    """Wire a :class:`RoninHTTPServer` to :class:`RoninHTTPHandler` and the factory.

    A pass ``("127.0.0.1", 0)`` binds an ephemeral port; the real one is then on
    ``server.server_address``, which is how a test drives the whole HTTP layer over a
    real loopback socket without racing to pick a free port.
    """
    return RoninHTTPServer(address, RoninHTTPHandler, open_agent=open_agent, log=log, clock=clock)


def _format_address(address: Any) -> str:
    """``host:port`` for the banner, whatever socket family ``server_address`` is."""
    if isinstance(address, tuple) and len(address) >= 2:
        return f"{address[0]}:{address[1]}"
    return str(address)


def serve_http(
    host: str,
    port: int,
    *,
    open_agent: AgentFactory,
    err: Callable[[str], None] | None = None,
    serve: bool = True,
) -> RoninHTTPServer:
    """Build the server, announce it on **stderr**, and serve until shutdown.

    The entry a ``ronin`` subcommand calls. The bound address goes to stderr — never
    stdout, which a caller may be piping a JSON response out of — and includes the three
    routes so a first run shows what to point a client at. ``serve=False`` returns the
    announced-but-idle server instead of blocking, which is how the announce path is
    tested; the parent's subcommand takes the default and blocks in ``serve_forever``.
    """
    server = build_server((host, port), open_agent=open_agent)
    write = err if err is not None else _stderr_write
    write(f"ronin http-api: listening on http://{_format_address(server.server_address)}\n")
    write(f"  POST {CHAT_COMPLETIONS_PATH}  (OpenAI chat completions)\n")
    write(f"  POST {MESSAGES_PATH}          (Anthropic messages)\n")
    write(f"  GET  {MODELS_PATH}\n")
    if serve:  # pragma: no cover - blocks until another thread calls shutdown()
        try:
            server.serve_forever()
        finally:
            server.server_close()
    return server


def _stderr_write(text: str) -> None:
    sys.stderr.write(text)


__all__ = [
    "CHAT_COMPLETIONS_PATH",
    "DEFAULT_MODEL",
    "MESSAGES_PATH",
    "MODELS_PATH",
    "AgentFactory",
    "BadRequest",
    "Response",
    "RoninHTTPHandler",
    "RoninHTTPServer",
    "anthropic_error",
    "build_server",
    "flatten_content",
    "last_user_text",
    "models_list",
    "openai_error",
    "route",
    "run_anthropic_messages",
    "run_openai_chat",
    "serve_http",
]
