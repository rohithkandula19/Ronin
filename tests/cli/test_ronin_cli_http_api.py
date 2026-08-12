"""``ronin`` behind an OpenAI/Anthropic-shaped HTTP API: the wire, and the loop behind it.

The claim under test is the one the endpoints exist to make: *a tool that speaks the
OpenAI or Anthropic wire format can point at Ronin and get an agent's answer back in the
shape it expects.* So the pure request-handling functions are exercised directly — no
socket — against a **real** :class:`~ronin.cli.sdk.Agent` over a scripted provider (the
same offline seam ``tests/cli/test_ronin_cli_serve.py`` uses), and one handler test
drives ``do_POST`` over in-memory streams to prove the thin HTTP layer moves those bytes
(the suite forbids ``socket``/``urllib`` outright — tests run offline — so a real
loopback round-trip is a policy impossibility here, not an oversight).

The split mirrors the module: :func:`run_openai_chat` / :func:`run_anthropic_messages`
and :func:`route` are pure and async and take an injected agent factory, so most of what
can go wrong with a request is a function call away; :func:`build_server` is the only
thing that needs a port.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path

import pytest
import stream_harness as h

from ronin.cli.http_api import (
    CHAT_COMPLETIONS_PATH,
    MESSAGES_PATH,
    MODELS_PATH,
    AgentFactory,
    BadRequest,
    RoninHTTPServer,
    anthropic_error,
    build_server,
    flatten_content,
    last_user_text,
    models_list,
    openai_error,
    route,
    run_anthropic_messages,
    run_openai_chat,
    serve_http,
)
from ronin.cli.sdk import Agent
from ronin.core.types import Mode

# --------------------------------------------------------------------------- #
# Offline agent factory — a real assembled agent over a scripted provider
# --------------------------------------------------------------------------- #


def workspace(root: Path) -> Path:
    """A minimal but real project for the agent to open. Never touches a network."""
    (root / "notes.md").write_text("the answer is four.\n", encoding="utf-8", newline="\n")
    return root


def agent_factory(root: Path, *, says: str) -> AgentFactory:
    """An :data:`~ronin.cli.http_api.AgentFactory` that yields one scripted-provider agent.

    Each call builds a fresh router with a fresh one-line script, so a factory can be
    invoked once per request exactly as the server invokes it — a read-only answer takes
    a single provider turn, which is the one response scripted here.
    """

    async def factory() -> Agent:
        router, _provider = h.scripted_router([h.provider_says(says)])
        return await Agent.open(
            workspace(root),
            router=router,
            mode=Mode.ASK,
            home=root / "home",
            environ={},
            record=False,
            connect_mcp=False,
        )

    return factory


# --------------------------------------------------------------------------- #
# The spec's required test: an OpenAI-SDK-shaped call returns an agent result
# --------------------------------------------------------------------------- #


async def test_an_openai_sdk_shaped_call_returns_the_agent_result(tmp_path: Path) -> None:
    """The exact body the OpenAI SDK sends for ``client.chat.completions.create``."""
    body = {"model": "ronin", "messages": [{"role": "user", "content": "what is 2+2?"}]}
    response = await run_openai_chat(body, open_agent=agent_factory(tmp_path, says="four."))

    assert response["object"] == "chat.completion"
    assert response["choices"][0]["message"]["role"] == "assistant"
    assert response["choices"][0]["message"]["content"] == "four."
    assert response["choices"][0]["finish_reason"] == "stop"
    # The model is echoed and the id is deterministic from the injected seq (0 here).
    assert response["model"] == "ronin"
    assert response["id"] == "chatcmpl-0"
    assert response["usage"]["total_tokens"] >= 0


async def test_an_anthropic_messages_call_handles_block_list_content(tmp_path: Path) -> None:
    """The Anthropic shape, exercising the block-list ``content`` a real client sends."""
    body = {
        "model": "ronin",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    }
    response = await run_anthropic_messages(body, open_agent=agent_factory(tmp_path, says="hello."))

    assert response["type"] == "message"
    assert response["role"] == "assistant"
    assert response["content"][0]["type"] == "text"
    assert response["content"][0]["text"] == "hello."
    assert response["stop_reason"] == "end_turn"
    assert response["model"] == "ronin"
    assert response["id"] == "msg_0"


# --------------------------------------------------------------------------- #
# Message extraction — the one place the two formats agree
# --------------------------------------------------------------------------- #


def test_last_user_text_reads_string_content() -> None:
    assert last_user_text({"messages": [{"role": "user", "content": "hello"}]}) == "hello"


def test_last_user_text_flattens_block_list_content() -> None:
    body = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
            }
        ]
    }
    assert last_user_text(body) == "a\nb"


def test_last_user_text_takes_the_last_user_turn_not_the_whole_transcript() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "an answer"},
            {"role": "user", "content": "second question"},
        ]
    }
    assert last_user_text(body) == "second question"


def test_flatten_content_keeps_text_blocks_and_drops_the_rest() -> None:
    content = [
        {"type": "image", "source": {"type": "base64", "data": "..."}},
        {"type": "text", "text": "describe this"},
    ]
    assert flatten_content(content) == "describe this"


def test_flatten_content_of_a_plain_string_is_the_string() -> None:
    assert flatten_content("just text") == "just text"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"messages": []},
        {"messages": "not a list"},
        {"messages": [{"role": "assistant", "content": "no user turn here"}]},
        {"messages": [{"role": "user", "content": ""}]},
        {"messages": [{"role": "user", "content": [{"type": "image", "source": {}}]}]},
        {"messages": ["not an object"]},
    ],
)
def test_malformed_messages_raise_bad_request(body: dict[str, object]) -> None:
    with pytest.raises(BadRequest):
        last_user_text(body)


@pytest.mark.parametrize("content", [42, [42], {"type": "text"}])
def test_malformed_content_raises_bad_request(content: object) -> None:
    with pytest.raises(BadRequest):
        flatten_content(content)


# --------------------------------------------------------------------------- #
# Error shaping — each provider's own envelope
# --------------------------------------------------------------------------- #


def test_openai_error_has_the_openai_envelope() -> None:
    err = openai_error("the field is required", code="missing_field")
    assert err["error"]["message"] == "the field is required"
    assert err["error"]["type"] == "invalid_request_error"
    assert err["error"]["code"] == "missing_field"


def test_anthropic_error_has_the_anthropic_envelope() -> None:
    err = anthropic_error("the field is required")
    assert err["type"] == "error"
    assert err["error"]["type"] == "invalid_request_error"
    assert err["error"]["message"] == "the field is required"


def test_models_list_advertises_one_honest_id() -> None:
    listing = models_list()
    assert listing["object"] == "list"
    assert listing["data"][0]["id"] == "ronin"
    assert listing["data"][0]["object"] == "model"


# --------------------------------------------------------------------------- #
# route(): the whole HTTP contract as a pure function, no socket
# --------------------------------------------------------------------------- #


async def test_route_runs_the_openai_loop_end_to_end(tmp_path: Path) -> None:
    body = json.dumps({"model": "ronin", "messages": [{"role": "user", "content": "hi"}]}).encode()
    response = await route(
        "POST",
        CHAT_COMPLETIONS_PATH,
        body,
        open_agent=agent_factory(tmp_path, says="four."),
        created=123,
        seq=7,
    )
    assert response.status == HTTPStatus.OK
    assert response.body["choices"][0]["message"]["content"] == "four."
    # The injected seq and created flow straight through, so ids and timestamps are stable.
    assert response.body["id"] == "chatcmpl-7"
    assert response.body["created"] == 123


async def test_route_missing_messages_is_a_shaped_openai_400(tmp_path: Path) -> None:
    """A body missing ``messages`` → the OpenAI error shape, via the validation path."""
    body = json.dumps({"model": "ronin"}).encode()
    response = await route(
        "POST", CHAT_COMPLETIONS_PATH, body, open_agent=agent_factory(tmp_path, says="unused.")
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert response.body["error"]["type"] == "invalid_request_error"
    assert "messages" in response.body["error"]["message"]


async def test_route_bad_json_on_the_messages_path_is_a_shaped_anthropic_400(
    tmp_path: Path,
) -> None:
    response = await route(
        "POST",
        MESSAGES_PATH,
        b"{not valid json",
        open_agent=agent_factory(tmp_path, says="unused."),
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert response.body["type"] == "error"
    assert response.body["error"]["type"] == "invalid_request_error"


async def test_route_serves_the_models_list(tmp_path: Path) -> None:
    response = await route(
        "GET", MODELS_PATH, b"", open_agent=agent_factory(tmp_path, says="unused.")
    )
    assert response.status == HTTPStatus.OK
    assert response.body["data"][0]["id"] == "ronin"


async def test_route_unknown_path_is_a_shaped_404(tmp_path: Path) -> None:
    response = await route(
        "POST", "/v1/embeddings", b"{}", open_agent=agent_factory(tmp_path, says="unused.")
    )
    assert response.status == HTTPStatus.NOT_FOUND
    assert response.body["error"]["type"] == "not_found_error"


async def test_route_surfaces_a_loop_failure_as_a_shaped_500() -> None:
    """A factory (or loop) that *raises* becomes a 500 with a message, not a traceback."""

    async def broken() -> Agent:
        raise RuntimeError("no model configuration found")

    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    response = await route("POST", CHAT_COMPLETIONS_PATH, body, open_agent=broken)
    assert response.status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.body["error"]["type"] == "api_error"
    assert "no model configuration found" in response.body["error"]["message"]


# --------------------------------------------------------------------------- #
# The HTTP layer — announced, and over a real loopback socket
# --------------------------------------------------------------------------- #


def test_serve_http_announces_the_bound_address_on_the_injected_stream(tmp_path: Path) -> None:
    """``serve=False`` returns the announced-but-idle server, so the banner is testable."""
    lines: list[str] = []
    server = serve_http(
        "127.0.0.1", 0, open_agent=agent_factory(tmp_path, says="x"), err=lines.append, serve=False
    )
    try:
        banner = "".join(lines)
        assert "ronin http-api: listening on http://127.0.0.1:" in banner
        assert CHAT_COMPLETIONS_PATH in banner
        assert MESSAGES_PATH in banner
        assert MODELS_PATH in banner
    finally:
        server.server_close()


def test_the_handler_routes_a_post_through_to_a_completion_without_a_socket(
    tmp_path: Path,
) -> None:
    """The wire, proven offline: ``do_POST`` reads a body, calls ``route`` and writes JSON.

    The repository forbids a network library (and even ``socket``) in tests — they run
    offline — so a real loopback round-trip is impossible here by policy. Instead the
    handler is driven directly: a ``BytesIO`` stands in for the request body and the
    response, and the assertion is on the bytes the handler wrote. This covers the
    ``do_POST`` → ``route`` → response-writing path the pure ``route`` tests cannot.
    """
    server = build_server(("127.0.0.1", 0), open_agent=agent_factory(tmp_path, says="four."))
    try:
        body = json.dumps(
            {"model": "ronin", "messages": [{"role": "user", "content": "what is 2+2?"}]}
        ).encode()
        request_bytes = (
            f"POST {CHAT_COMPLETIONS_PATH} HTTP/1.1\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode() + body
        written = _drive_handler(server, request_bytes)
    finally:
        server.server_close()

    assert f"{HTTPStatus.OK.value} " in written.split("\r\n", 1)[0]
    payload = json.loads(written.split("\r\n\r\n", 1)[1])
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "four."
    assert payload["id"] == "chatcmpl-1", "the server's counter assigns the first id"


def _drive_handler(server: RoninHTTPServer, request_bytes: bytes) -> str:
    """Run one HTTP request through the handler with in-memory streams — no socket.

    Builds the handler without its socket-touching ``__init__`` (which would call
    ``handle_one_request`` against a real connection), wires a ``BytesIO`` request and
    response, and invokes the method the request line names. Returns the raw response.
    """
    import io

    from ronin.cli.http_api import RoninHTTPHandler

    handler = RoninHTTPHandler.__new__(RoninHTTPHandler)
    handler.rfile = io.BytesIO(request_bytes)
    handler.wfile = io.BytesIO()
    handler.server = server
    handler.client_address = ("127.0.0.1", 0)
    handler.close_connection = True
    handler.handle_one_request()  # reads the request line + headers, dispatches do_POST
    written = handler.wfile.getvalue()
    assert isinstance(written, bytes)
    return written.decode("utf-8", errors="replace")
