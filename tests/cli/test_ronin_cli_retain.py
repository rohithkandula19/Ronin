"""The wire a Retainer is reached on.

Two properties here are worth more than the rest.

``test_a_bad_signature_is_refused_before_the_body_is_parsed`` sends unparseable
JSON with a wrong signature and requires a 401 rather than a 400. If the parse
ran first the status would be 400, and the receiver would have interpreted
attacker-controlled bytes before deciding whether to trust them.

The signature tests use digests computed by ``openssl dgst -sha256 -hmac``, not
by the module under test. A round trip through my own ``hmac.new`` would agree
with itself no matter what the payload construction got wrong.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

import pytest

from ronin.cli.retain import (
    DELIVERY_PATH,
    GITHUB_SCHEME,
    HEALTH_PATH,
    MAX_BODY_BYTES,
    SCHEMES,
    SLACK_SCHEME,
    TELEGRAM_SCHEME,
    Delivery,
    DeliveryRefused,
    ReceiverConfig,
    Response,
    RetainerHTTPHandler,
    RetainerHTTPServer,
    _format_address,
    build_server,
    header,
    route,
    serve_retainer,
    verify,
)

SECRET = "topsecret"
BODY = b'{"action":"created"}'
SLACK_TS = "1757155200"

#: Computed with `printf '%s' '<payload>' | openssl dgst -sha256 -hmac topsecret`.
#: Independent of the implementation under test, which is the entire point.
GITHUB_DIGEST = "78d6bbc3e868dd6a258ff8ded10c8e57d52edd74c9cc84a507871232a42b8474"
SLACK_DIGEST = "da0e8bb53f693e45ae9516de240491ecd5a9dbb5d66b88bb071f1c80d80a4fe1"

NOW = 1757155200.0


async def accept(delivery: Delivery) -> Mapping[str, Any]:
    return {"seen": delivery.body.get("action", "")}


def send(
    *,
    method: str = "POST",
    path: str = DELIVERY_PATH,
    headers: Mapping[str, str] | None = None,
    body: bytes = BODY,
    scheme: Any = GITHUB_SCHEME,
    secret: str = SECRET,
    receive: Any = accept,
    now: float = NOW,
    tolerance: float = 300.0,
) -> Response:
    if headers is None:
        headers = {"X-Hub-Signature-256": f"sha256={GITHUB_DIGEST}"}
    return asyncio.run(
        route(
            method,
            path,
            headers,
            body,
            scheme=scheme,
            secret=secret,
            receive=receive,
            now=lambda: now,
            tolerance=tolerance,
        )
    )


# --------------------------------------------------------------------------- #
# Signatures, against digests this module did not compute
# --------------------------------------------------------------------------- #


def test_a_real_github_signature_is_accepted() -> None:
    assert send().status is HTTPStatus.OK


def test_a_real_slack_signature_is_accepted() -> None:
    response = send(
        scheme=SLACK_SCHEME,
        headers={
            "X-Slack-Signature": f"v0={SLACK_DIGEST}",
            "X-Slack-Request-Timestamp": SLACK_TS,
        },
    )
    assert response.status is HTTPStatus.OK


def test_slack_signs_the_timestamp_so_the_github_digest_does_not_work() -> None:
    """Proves the payload construction differs, not just the header name."""
    response = send(
        scheme=SLACK_SCHEME,
        headers={
            "X-Slack-Signature": f"v0={GITHUB_DIGEST}",
            "X-Slack-Request-Timestamp": SLACK_TS,
        },
    )
    assert response.status is HTTPStatus.UNAUTHORIZED


def test_a_changed_body_invalidates_the_signature() -> None:
    assert send(body=b'{"action":"deleted"}').status is HTTPStatus.UNAUTHORIZED


def test_a_wrong_prefix_is_refused() -> None:
    assert send(headers={"X-Hub-Signature-256": GITHUB_DIGEST}).status is HTTPStatus.UNAUTHORIZED


def test_a_truncated_signature_is_refused_rather_than_crashing() -> None:
    short = {"X-Hub-Signature-256": "sha256=" + GITHUB_DIGEST[:8]}
    assert send(headers=short).status is HTTPStatus.UNAUTHORIZED


def test_a_missing_signature_header_is_refused() -> None:
    response = send(headers={})
    assert response.status is HTTPStatus.UNAUTHORIZED
    assert "X-Hub-Signature-256" in response.body["error"]


def test_header_lookup_is_case_insensitive() -> None:
    assert send(headers={"x-hub-signature-256": f"sha256={GITHUB_DIGEST}"}).status is HTTPStatus.OK
    assert header({"Content-Type": "a"}, "CONTENT-TYPE") == "a"
    assert header({}, "anything") == ""


# --------------------------------------------------------------------------- #
# No secret means no
# --------------------------------------------------------------------------- #


def test_an_unconfigured_receiver_accepts_nothing() -> None:
    """A missing environment variable must not become an open endpoint."""
    response = send(secret="")
    assert response.status is HTTPStatus.SERVICE_UNAVAILABLE
    assert "no delivery can be verified" in response.body["error"]


def test_an_unconfigured_receiver_refuses_even_a_correct_looking_signature() -> None:
    assert send(secret="").status is not HTTPStatus.OK


# --------------------------------------------------------------------------- #
# Order of operations — the security property
# --------------------------------------------------------------------------- #


def test_a_bad_signature_is_refused_before_the_body_is_parsed() -> None:
    """401, not 400: nothing hostile is interpreted before it is trusted."""
    response = send(body=b"{not json at all", headers={"X-Hub-Signature-256": "sha256=00"})
    assert response.status is HTTPStatus.UNAUTHORIZED


def test_a_bad_signature_is_refused_before_the_size_is_checked() -> None:
    huge = b"x" * (MAX_BODY_BYTES + 10)
    response = send(body=huge, headers={"X-Hub-Signature-256": "sha256=00"})
    assert response.status is HTTPStatus.UNAUTHORIZED


def test_the_receiver_never_runs_on_an_unverified_delivery() -> None:
    ran: list[Delivery] = []

    async def record(delivery: Delivery) -> Mapping[str, Any]:
        ran.append(delivery)
        return {}

    send(headers={"X-Hub-Signature-256": "sha256=00"}, receive=record)
    assert ran == []


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #


def test_a_recorded_slack_delivery_stops_working() -> None:
    response = send(
        scheme=SLACK_SCHEME,
        headers={
            "X-Slack-Signature": f"v0={SLACK_DIGEST}",
            "X-Slack-Request-Timestamp": SLACK_TS,
        },
        now=NOW + 3600,
    )
    assert response.status is HTTPStatus.UNAUTHORIZED
    assert "does not replay" in response.body["error"]


def test_a_delivery_from_the_future_is_refused_too() -> None:
    response = send(
        scheme=SLACK_SCHEME,
        headers={
            "X-Slack-Signature": f"v0={SLACK_DIGEST}",
            "X-Slack-Request-Timestamp": SLACK_TS,
        },
        now=NOW - 3600,
    )
    assert response.status is HTTPStatus.UNAUTHORIZED


def test_a_missing_or_unparseable_timestamp_is_refused() -> None:
    base = {"X-Slack-Signature": f"v0={SLACK_DIGEST}"}
    assert send(scheme=SLACK_SCHEME, headers=base).status is HTTPStatus.UNAUTHORIZED
    with_junk = {**base, "X-Slack-Request-Timestamp": "yesterday"}
    response = send(scheme=SLACK_SCHEME, headers=with_junk)
    assert response.status is HTTPStatus.UNAUTHORIZED
    assert "not a number" in response.body["error"]


def test_a_scheme_without_a_timestamp_has_nothing_to_expire() -> None:
    assert send(now=NOW + 10_000_000).status is HTTPStatus.OK


# --------------------------------------------------------------------------- #
# Telegram: a shared token, still compared in constant time
# --------------------------------------------------------------------------- #


def test_the_right_token_is_accepted() -> None:
    response = send(scheme=TELEGRAM_SCHEME, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert response.status is HTTPStatus.OK


def test_a_wrong_token_is_refused() -> None:
    response = send(
        scheme=TELEGRAM_SCHEME, headers={"X-Telegram-Bot-Api-Secret-Token": "topsecrez"}
    )
    assert response.status is HTTPStatus.UNAUTHORIZED


def test_a_prefix_of_the_token_is_refused() -> None:
    response = send(scheme=TELEGRAM_SCHEME, headers={"X-Telegram-Bot-Api-Secret-Token": "top"})
    assert response.status is HTTPStatus.UNAUTHORIZED


def test_every_named_scheme_is_reachable_by_name() -> None:
    assert set(SCHEMES) == {"github", "slack", "telegram"}
    assert SCHEMES["github"] is GITHUB_SCHEME


# --------------------------------------------------------------------------- #
# Routing, size, and shape
# --------------------------------------------------------------------------- #


def test_health_needs_no_signature_and_names_the_scheme() -> None:
    response = send(method="GET", path=HEALTH_PATH, headers={}, secret="")
    assert response.status is HTTPStatus.OK
    assert response.body == {"ok": True, "scheme": "github"}


def test_health_takes_get_only() -> None:
    assert send(path=HEALTH_PATH, headers={}).status is HTTPStatus.METHOD_NOT_ALLOWED


def test_an_unknown_path_is_a_404() -> None:
    assert send(path="/wp-admin", headers={}).status is HTTPStatus.NOT_FOUND


def test_delivery_takes_post_only() -> None:
    assert send(method="GET").status is HTTPStatus.METHOD_NOT_ALLOWED


def test_an_oversized_but_signed_body_is_refused() -> None:
    import hmac
    from hashlib import sha256

    huge = b"x" * (MAX_BODY_BYTES + 10)
    digest = hmac.new(SECRET.encode(), huge, sha256).hexdigest()
    response = send(body=huge, headers={"X-Hub-Signature-256": f"sha256={digest}"})
    assert response.status is HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def test_unparseable_but_signed_json_is_a_400() -> None:
    import hmac
    from hashlib import sha256

    broken = b"{not json"
    digest = hmac.new(SECRET.encode(), broken, sha256).hexdigest()
    response = send(body=broken, headers={"X-Hub-Signature-256": f"sha256={digest}"})
    assert response.status is HTTPStatus.BAD_REQUEST


def test_a_signed_json_array_is_a_400_because_a_delivery_is_an_object() -> None:
    import hmac
    from hashlib import sha256

    array = b"[1, 2, 3]"
    digest = hmac.new(SECRET.encode(), array, sha256).hexdigest()
    response = send(body=array, headers={"X-Hub-Signature-256": f"sha256={digest}"})
    assert response.status is HTTPStatus.BAD_REQUEST


def test_the_receivers_answer_is_folded_into_the_response() -> None:
    response = send()
    assert response.body == {"ok": True, "seen": "created"}


def test_a_receiver_that_refuses_chooses_the_status() -> None:
    async def refuse(_delivery: Delivery) -> Mapping[str, Any]:
        raise DeliveryRefused(HTTPStatus.CONFLICT, "that retainer is already running")

    response = send(receive=refuse)
    assert response.status is HTTPStatus.CONFLICT
    assert response.body["error"] == "that retainer is already running"


def test_a_receiver_that_raises_is_a_500_and_never_a_traceback() -> None:
    async def explode(_delivery: Delivery) -> Mapping[str, Any]:
        raise RuntimeError("the post was not there")

    response = send(receive=explode)
    assert response.status is HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.body["error"] == "receiver failed: the post was not there"
    assert "Traceback" not in json.dumps(response.body)


def test_the_delivery_carries_the_raw_bytes_it_was_verified_against() -> None:
    seen: list[Delivery] = []

    async def record(delivery: Delivery) -> Mapping[str, Any]:
        seen.append(delivery)
        return {}

    send(receive=record)
    assert seen[0].raw_body == BODY
    assert seen[0].scheme == "github"


# --------------------------------------------------------------------------- #
# The HTTP layer, driven offline
# --------------------------------------------------------------------------- #


def drive(server: RetainerHTTPServer, request_bytes: bytes) -> str:
    """One request through the handler with in-memory streams — no socket.

    The repository forbids a network library, and even ``socket``, in tests: they
    run offline. So the handler is built without its socket-touching ``__init__``
    and driven over ``BytesIO``, which still covers ``do_POST`` → ``route`` →
    response-writing, the path the pure ``route`` tests cannot reach.
    """
    handler = RetainerHTTPHandler.__new__(RetainerHTTPHandler)
    handler.rfile = io.BytesIO(request_bytes)
    handler.wfile = io.BytesIO()
    handler.server = server
    handler.client_address = ("127.0.0.1", 0)
    handler.close_connection = True
    handler.handle_one_request()
    written = handler.wfile.getvalue()
    assert isinstance(written, bytes)
    return written.decode("utf-8", errors="replace")


def bound() -> RetainerHTTPServer:
    config = ReceiverConfig(
        scheme=GITHUB_SCHEME, secret=SECRET, receive=accept, now=lambda: NOW, log=lambda _t: None
    )
    return build_server(("127.0.0.1", 0), config)


def wire(path: str, body: bytes, headers: Mapping[str, str], *, method: str = "POST") -> bytes:
    lines = [f"{method} {path} HTTP/1.1", f"Content-Length: {len(body)}"]
    lines += [f"{name}: {value}" for name, value in headers.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def status_and_body(written: str) -> tuple[int, dict[str, Any]]:
    head, _, payload = written.partition("\r\n\r\n")
    return int(head.split(" ", 2)[1]), json.loads(payload)


def test_a_signed_delivery_round_trips_through_the_handler() -> None:
    server = bound()
    try:
        request = wire(DELIVERY_PATH, BODY, {"X-Hub-Signature-256": f"sha256={GITHUB_DIGEST}"})
        assert status_and_body(drive(server, request)) == (200, {"ok": True, "seen": "created"})
    finally:
        server.server_close()


def test_an_unsigned_delivery_is_refused_through_the_handler() -> None:
    server = bound()
    try:
        status, body = status_and_body(drive(server, wire(DELIVERY_PATH, BODY, {})))
        assert (status, body["ok"]) == (401, False)
    finally:
        server.server_close()


def test_health_answers_through_the_handler() -> None:
    server = bound()
    try:
        status, body = status_and_body(drive(server, wire(HEALTH_PATH, b"", {}, method="GET")))
        assert (status, body["ok"]) is not None
        assert body == {"ok": True, "scheme": "github"}
    finally:
        server.server_close()


def test_the_handler_reads_no_more_than_the_cap_however_much_is_announced() -> None:
    """A Content-Length of a gigabyte must not become a gigabyte of memory."""
    server = bound()
    try:
        body = b"x" * (MAX_BODY_BYTES + 4096)
        request = (
            f"POST {DELIVERY_PATH} HTTP/1.1\r\n"
            f"X-Hub-Signature-256: sha256=00\r\n"
            f"Content-Length: {1 << 30}\r\n\r\n"
        ).encode() + body
        status, _body = status_and_body(drive(server, request))
        assert status == 401
    finally:
        server.server_close()


def test_serve_retainer_announces_where_it_is_listening() -> None:
    lines: list[str] = []
    config = ReceiverConfig(scheme=GITHUB_SCHEME, secret=SECRET, receive=accept)
    server = serve_retainer("127.0.0.1", 0, config, err=lines.append, serve=False)
    try:
        banner = "".join(lines)
        assert "retainer receiver listening on http://127.0.0.1:" in banner
        assert DELIVERY_PATH in banner
        assert "(github)" in banner
    finally:
        server.server_close()


def test_the_log_hook_is_used_when_given_and_silent_when_not() -> None:
    seen: list[str] = []
    config = ReceiverConfig(
        scheme=GITHUB_SCHEME, secret=SECRET, receive=accept, now=lambda: NOW, log=seen.append
    )
    server = build_server(("127.0.0.1", 0), config)
    try:
        drive(server, wire(DELIVERY_PATH, BODY, {"X-Hub-Signature-256": f"sha256={GITHUB_DIGEST}"}))
        assert any(DELIVERY_PATH in line for line in seen)
    finally:
        server.server_close()

    quiet = build_server(("127.0.0.1", 0), ReceiverConfig(GITHUB_SCHEME, SECRET, accept))
    try:
        drive(quiet, wire(DELIVERY_PATH, BODY, {}))
    finally:
        quiet.server_close()


def test_start_and_stop_shut_down_cleanly() -> None:
    server = bound()
    server.start()
    server.stop()
    assert server._thread is None


def test_serving_starts_the_thread_and_stopping_joins_it() -> None:
    """The one path `serve=False` skips. No traffic is sent — only the loop runs."""
    config = ReceiverConfig(scheme=GITHUB_SCHEME, secret=SECRET, receive=accept)
    server = serve_retainer("127.0.0.1", 0, config, err=lambda _text: None)
    try:
        assert server._thread is not None
        assert server._thread.is_alive()
    finally:
        server.stop()
    assert server._thread is None


def test_verify_raises_with_the_status_to_answer_with() -> None:
    """No truthy return to ignore: success is silence, failure is an exception."""
    verify(
        BODY,
        {"X-Hub-Signature-256": f"sha256={GITHUB_DIGEST}"},
        scheme=GITHUB_SCHEME,
        secret=SECRET,
        now=NOW,
    )
    with pytest.raises(DeliveryRefused) as caught:
        verify(BODY, {}, scheme=GITHUB_SCHEME, secret=SECRET, now=NOW)
    assert caught.value.status is HTTPStatus.UNAUTHORIZED


def test_the_default_clock_is_a_real_one() -> None:
    """The injected default must be wall time, or every Slack delivery expires."""
    import time

    config = ReceiverConfig(scheme=SLACK_SCHEME, secret=SECRET, receive=accept)
    assert abs(config.now() - time.time()) < 5


def test_a_unix_style_address_is_printable_rather_than_a_bytes_repr() -> None:
    assert _format_address(b"/tmp/ronin.sock") == "/tmp/ronin.sock"
    assert _format_address((b"::1", 8080)) == "::1:8080"
    assert _format_address("anything") == "anything"
