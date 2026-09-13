"""The watch stream: a running session, seen from somewhere else.

Two halves, matching the module. The framing, the resumption arithmetic and the token
check are pure functions asserted directly. The server half is driven over a **real
loopback socket** with `urllib`, because the things that go wrong with an event stream
— a header a client actually sends, a proxy-hostile Content-Type, a connection that
dies mid-turn — are not visible from a function call.

The stream carries session contents, so the token tests are not ceremony: a `401`
that ever became a `200` would publish someone's source tree to whoever can reach the
port. Every server here binds port 0 on loopback and is closed in a `finally`.
"""

from __future__ import annotations

import io
import json
from typing import Any
from urllib.parse import quote

import pytest
import stream_harness as h

from ronin.cli.main import Options, Usage, parse
from ronin.cli.sdk import Agent
from ronin.cli.watch import (
    RETRY_MS,
    WATCH_PATH,
    WatchHandler,
    authorized,
    bound_address,
    build_watch_server,
    headers_of,
    mint_token,
    since_from,
    sse_comment,
    sse_frame,
)
from ronin.core.fanout import Delivery, EventHub
from ronin.core.types import Error, Mode, TextDelta, ToolStart, TurnEnd, TurnState

TOKEN = "a-test-token"


def delivery(seq: int, text: str = "hello", *, missed: int = 0) -> Delivery:
    return Delivery(seq=seq, event=TextDelta(text=text), missed=missed)


# --------------------------------------------------------------------------- #
# framing
# --------------------------------------------------------------------------- #


def test_a_frame_carries_the_sequence_number_as_the_event_id() -> None:
    """``id`` is what comes back as ``Last-Event-ID``; the two halves are one number."""
    frame = sse_frame(delivery(7))
    assert frame.startswith("id: 7\n")
    assert frame.endswith("\n\n")


def test_a_frame_names_its_event_type() -> None:
    assert "event: text_delta\n" in sse_frame(delivery(1))
    assert "event: turn_end\n" in sse_frame(
        Delivery(seq=2, event=TurnEnd(turn_index=0, state=TurnState.DONE))
    )


def test_the_payload_is_the_same_json_the_headless_renderer_emits() -> None:
    """One wire shape for the session's events, not a second one that drifts from it."""
    body = _data(sse_frame(Delivery(seq=3, event=ToolStart(tool_use_id="t1", name="bash"))))
    assert body["type"] == "tool_start"
    assert body["name"] == "bash"
    assert body["seq"] == 3


def test_a_gap_is_carried_in_the_payload_rather_than_dropped() -> None:
    """A watcher that is not told about a hole draws the hole as continuity."""
    assert _data(sse_frame(delivery(9, missed=4)))["missed"] == 4


def test_no_missed_key_when_nothing_was_missed() -> None:
    assert "missed" not in _data(sse_frame(delivery(9)))


def test_a_payload_with_a_newline_in_it_stays_one_frame() -> None:
    """A frame split across raw lines reads to the client as a truncated event."""
    frame = sse_frame(Delivery(seq=1, event=Error(message="line one\nline two")))
    assert frame.count("data: ") == 1  # json escaped the newline: one data line
    assert _data(frame)["message"] == "line one\nline two"
    # And every line before the terminating blank one is a field, not loose text.
    assert all(": " in line for line in frame.rstrip("\n").split("\n"))


def test_a_comment_frame_is_a_comment() -> None:
    assert sse_comment("keep-alive") == ": keep-alive\n\n"


def _data(frame: str) -> dict[str, Any]:
    line = next(part for part in frame.split("\n") if part.startswith("data: "))
    parsed = json.loads(line[len("data: ") :])
    assert isinstance(parsed, dict)
    return parsed


# --------------------------------------------------------------------------- #
# where a watcher resumes from
# --------------------------------------------------------------------------- #


def test_a_fresh_watcher_replays_what_is_held() -> None:
    """Opening on half a sentence, with no TurnStart, is not a view of a session."""
    assert since_from({}, {}) == 0


def test_last_event_id_is_where_a_reconnect_resumes() -> None:
    assert since_from({"last-event-id": "41"}, {}) == 41


def test_the_header_wins_over_a_query_string() -> None:
    """The browser rewrites the header on reconnect; the URL keeps its original value.

    Letting the stale query win would re-replay the whole ring on every blink.
    """
    assert since_from({"last-event-id": "41"}, {"since": ["3"]}) == 41


def test_a_query_string_works_for_a_client_that_cannot_set_headers() -> None:
    assert since_from({}, {"since": ["12"]}) == 12


@pytest.mark.parametrize("raw", ["", "  ", "abc", "12abc", "NaN"])
def test_a_mangled_resume_point_replays_instead_of_refusing(raw: str) -> None:
    """A watcher that gets a 400 on reconnect is a watcher that stays dark."""
    assert since_from({"last-event-id": raw}, {}) == 0


def test_a_negative_resume_point_is_the_beginning_not_an_error() -> None:
    assert since_from({"last-event-id": "-5"}, {}) == 0


def test_headers_are_matched_however_the_client_spelled_them() -> None:
    assert headers_of({"Last-Event-ID": "9"}) == {"last-event-id": "9"}
    assert since_from(headers_of({"LAST-EVENT-ID": "9"}), {}) == 9


# --------------------------------------------------------------------------- #
# the token
# --------------------------------------------------------------------------- #


def test_a_bearer_token_authorizes() -> None:
    assert authorized(TOKEN, {"authorization": f"Bearer {TOKEN}"}, {})


def test_the_scheme_is_matched_case_insensitively() -> None:
    assert authorized(TOKEN, {"authorization": f"bearer {TOKEN}"}, {})


def test_a_query_token_authorizes_because_eventsource_cannot_set_headers() -> None:
    assert authorized(TOKEN, {}, {"token": [TOKEN]})


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": ""},
        {"authorization": "Bearer "},
        {"authorization": TOKEN},  # no scheme
        {"authorization": f"Basic {TOKEN}"},
        {"authorization": f"Bearer {TOKEN}x"},
        {"authorization": f"Bearer {TOKEN[:-1]}"},
    ],
)
def test_anything_short_of_the_token_does_not(headers: dict[str, str]) -> None:
    assert not authorized(TOKEN, headers, {})


def test_a_server_with_no_token_authorizes_nobody() -> None:
    """The failure this would be blamed for: a tokenless server serving every session."""
    assert not authorized("", {"authorization": "Bearer "}, {})
    assert not authorized("", {}, {"token": [""]})


def test_a_server_cannot_be_built_without_a_token() -> None:
    """Refused at bind, not at the first request — a server that binds looks like it works."""
    with pytest.raises(ValueError, match="needs a token"):
        build_watch_server(("127.0.0.1", 0), hub=EventHub(), token="")


def test_a_minted_token_is_long_and_url_safe() -> None:
    token = mint_token()
    assert len(token) >= 32
    assert token == quote(token, safe="-_")
    assert mint_token() != token


def test_the_banner_address_survives_every_socket_family() -> None:
    assert bound_address(("127.0.0.1", 8080)) == "127.0.0.1:8080"
    assert bound_address((b"127.0.0.1", 8080)) == "127.0.0.1:8080"
    assert bound_address("/tmp/sock") == "/tmp/sock"


# --------------------------------------------------------------------------- #
# the handler, driven without a socket
# --------------------------------------------------------------------------- #
#
# `scripts/check_test_imports.py` forbids a network library in tests, with no
# allowlist and a docstring explaining why one must not be added casually. So the
# handler is driven the way `test_ronin_cli_http_api.py` drives its own: a `BytesIO`
# for the request, a writable stand-in for the response, and assertions on the bytes
# written. Everything in `WatchHandler` runs — routing, the token check, the headers,
# the streaming loop; the only thing left out is the socket, which is stdlib's.
#
# It is also the better test. There is no port to race for and no sleep to tune, and
# "the watcher's connection died" is a `wfile` that raises rather than a timing trick.


def drive(
    hub: EventHub,
    request: str,
    *,
    token: str = TOKEN,
    keepalive: float = 60.0,
    wfile: io.BytesIO | None = None,
) -> str:
    """One request through the real handler. Returns everything it wrote."""
    server = build_watch_server(("127.0.0.1", 0), hub=hub, token=token, keepalive=keepalive)
    try:
        handler = WatchHandler.__new__(WatchHandler)
        handler.rfile = io.BytesIO(request.encode("utf-8"))
        handler.wfile = io.BytesIO() if wfile is None else wfile
        handler.server = server
        handler.client_address = ("127.0.0.1", 0)
        handler.close_connection = True
        handler.handle_one_request()
        written = handler.wfile.getvalue()
    finally:
        server.server_close()
    assert isinstance(written, bytes)
    return written.decode("utf-8", errors="replace")


def get(path: str, **headers: str) -> str:
    """A raw GET request, with headers spelled as a client would spell them."""
    lines = [f"GET {path} HTTP/1.1", "Host: localhost"]
    lines.extend(f"{name.replace('_', '-')}: {value}" for name, value in headers.items())
    return "\r\n".join(lines) + "\r\n\r\n"


def status(written: str) -> int:
    return int(written.split("\r\n", 1)[0].split(" ")[1])


def frames(written: str) -> list[dict[str, Any]]:
    """Every event frame's payload, in order. Comments and the retry frame skipped."""
    body = written.split("\r\n\r\n", 1)[1]
    return [
        json.loads(line[len("data: ") :]) for line in body.split("\n") if line.startswith("data: ")
    ]


def closed_hub(*events: Any) -> EventHub:
    """A hub holding ``events`` with the session already over, so a stream terminates."""
    hub = EventHub()
    for event in events:
        hub.publish(event)
    hub.close()
    return hub


def test_a_request_without_a_token_is_refused_before_a_single_event() -> None:
    written = drive(closed_hub(TextDelta(text="secret source code")), get(WATCH_PATH))
    assert status(written) == 401
    assert "secret source code" not in written


@pytest.mark.parametrize("offered", ["", "x", TOKEN[:-1], TOKEN + "x"])
def test_the_refusal_says_nothing_about_the_token(offered: str) -> None:
    """Every one of "absent", "short", "wrong" and "long" is a fact about the secret."""
    written = drive(closed_hub(), get(WATCH_PATH, Authorization=f"Bearer {offered}"))
    assert status(written) == 401
    assert written.endswith(json.dumps({"error": "a watch token is required"}))


def test_an_authorized_watcher_gets_the_backlog_and_the_end_of_the_turn() -> None:
    hub = closed_hub(
        TextDelta(text="before"),
        TextDelta(text="after"),
        TurnEnd(turn_index=0, state=TurnState.DONE),
    )
    written = drive(hub, get(f"{WATCH_PATH}?token={TOKEN}"))
    assert status(written) == 200
    assert "Content-Type: text/event-stream" in written
    assert [frame.get("text") for frame in frames(written)[:2]] == ["before", "after"]
    assert frames(written)[-1]["type"] == "turn_end"


def test_the_stream_opens_with_a_retry_hint() -> None:
    """So a two-second blip does not get the browser's default back-off."""
    written = drive(closed_hub(), get(f"{WATCH_PATH}?token={TOKEN}"))
    assert written.split("\r\n\r\n", 1)[1].startswith(f"retry: {RETRY_MS}\n\n")


def test_a_bearer_header_works_as_well_as_the_query_string() -> None:
    written = drive(
        closed_hub(TextDelta(text="hi")), get(WATCH_PATH, Authorization=f"Bearer {TOKEN}")
    )
    assert status(written) == 200
    assert frames(written)[0]["text"] == "hi"


def test_a_reconnect_resumes_from_last_event_id() -> None:
    hub = closed_hub(*(TextDelta(text=f"chunk {index}") for index in range(4)))
    written = drive(hub, get(f"{WATCH_PATH}?token={TOKEN}", Last_Event_ID="2"))
    assert [frame["seq"] for frame in frames(written)] == [3, 4]


def test_a_reconnect_past_the_ring_is_told_what_it_lost() -> None:
    """The whole point of the gap being in the payload: it survives the wire."""
    hub = EventHub(capacity=2)
    for index in range(6):
        hub.publish(TextDelta(text=f"chunk {index}"))
    hub.close()
    written = drive(hub, get(f"{WATCH_PATH}?token={TOKEN}", Last_Event_ID="1"))
    assert frames(written)[0]["missed"] == 3


def test_the_stream_is_marked_unbufferable_for_whatever_is_in_the_way() -> None:
    """A proxy that buffers an event stream makes a live turn look like a hang."""
    written = drive(closed_hub(), get(f"{WATCH_PATH}?token={TOKEN}"))
    assert "Cache-Control: no-cache, no-store" in written
    assert "X-Accel-Buffering: no" in written


def test_a_silent_stream_sends_a_keep_alive_rather_than_being_closed() -> None:
    hub = EventHub()

    class EndOnKeepAlive(io.BytesIO):
        """Ends the session the moment the first keep-alive reaches the wire."""

        def write(self, data: Any) -> int:
            if data.startswith(b": "):
                hub.close()
            return super().write(data)

    written = drive(hub, get(f"{WATCH_PATH}?token={TOKEN}"), keepalive=0.01, wfile=EndOnKeepAlive())
    assert ": keep-alive\n\n" in written


def test_a_watcher_that_disappears_does_not_reach_the_session() -> None:
    """A closed tab is the normal end of a watch, not an error in the terminal."""
    hub = closed_hub(*(TextDelta(text=f"chunk {index}") for index in range(5)))

    class Hangup(io.BytesIO):
        def write(self, data: Any) -> int:
            written = super().write(data)
            if b"chunk 1" in data:
                raise BrokenPipeError("the tab was closed")
            return written

    # No exception escapes, and the session is untouched by the watcher leaving.
    drive(hub, get(f"{WATCH_PATH}?token={TOKEN}"), wfile=Hangup())
    assert hub.closed


def test_another_path_is_a_404_not_a_stream() -> None:
    written = drive(closed_hub(TextDelta(text="secret")), get(f"/admin?token={TOKEN}"))
    assert status(written) == 404
    assert "secret" not in written


def test_the_stream_is_read_only() -> None:
    """Nothing here accepts a prompt, an approval or a steer, and it says so."""
    request = f"POST {WATCH_PATH}?token={TOKEN} HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n"
    written = drive(closed_hub(), request)
    assert status(written) == 405
    assert "read-only" in written


def test_two_watchers_see_the_same_turn() -> None:
    hub = closed_hub(TextDelta(text="to both"))
    first = drive(hub, get(f"{WATCH_PATH}?token={TOKEN}"))
    second = drive(hub, get(f"{WATCH_PATH}?token={TOKEN}"))
    assert frames(first)[0]["text"] == "to both"
    assert frames(second)[0]["text"] == "to both"


def test_a_server_binds_and_announces_its_real_port() -> None:
    """Port 0 means "any free one", and the banner must name the one it got."""
    hub = EventHub()
    server = build_watch_server(("127.0.0.1", 0), hub=hub, token=TOKEN)
    try:
        where = bound_address(server.server_address)
        assert where.startswith("127.0.0.1:")
        assert int(where.split(":")[1]) > 0
    finally:
        server.server_close()


# --------------------------------------------------------------------------- #
# attached to a session
# --------------------------------------------------------------------------- #


async def test_a_watched_agent_sends_every_event_both_ways(tmp_path: Any) -> None:
    """The terminal keeps its stream; the watchers get a copy taken on the way past.

    Teed inside ``Agent.stream`` rather than at each front end, so a renderer added
    later cannot forget to cooperate — a watcher that silently sees nothing is worse
    than one that cannot connect.
    """
    router, _provider = h.scripted_router([h.provider_says("done")])
    agent = await Agent.open(
        tmp_path,
        router=router,
        mode=Mode.ASK,
        home=tmp_path / "home",
        environ={},
        record=False,
        connect_mcp=False,
    )
    hub = EventHub()
    watcher = hub.subscribe(since=0)
    try:
        agent.watched(hub)
        terminal = [event async for event in agent.stream("hello", verify=False)]
    finally:
        hub.close()
        await agent.aclose()
    watched = [delivery.event async for delivery in watcher]
    assert terminal == watched
    assert terminal, "the turn produced no events at all"


async def test_detaching_stops_the_copy_without_stopping_the_session(tmp_path: Any) -> None:
    router, _provider = h.scripted_router([h.provider_says("one"), h.provider_says("two")])
    agent = await Agent.open(
        tmp_path,
        router=router,
        mode=Mode.ASK,
        home=tmp_path / "home",
        environ={},
        record=False,
        connect_mcp=False,
    )
    hub = EventHub()
    try:
        agent.watched(hub)
        [event async for event in agent.stream("first", verify=False)]
        during = hub.subscribe().cursor
        agent.watched(None)
        second = [event async for event in agent.stream("second", verify=False)]
    finally:
        hub.close()
        await agent.aclose()
    assert second, "the session must keep running once nobody is watching"
    assert hub.subscribe().cursor == during


# --------------------------------------------------------------------------- #
# reachable from the command line
# --------------------------------------------------------------------------- #


def test_the_flag_asks_for_an_ephemeral_port_by_default() -> None:
    parsed = parse(["--watch", "do", "the", "thing"])
    assert isinstance(parsed, Options)
    assert parsed.watch_port == 0
    assert parsed.prompt == "do the thing"


def test_the_flag_does_not_eat_the_prompt() -> None:
    """The reason the port is its own flag.

    Ronin takes its prompt as bare words, so `--watch [PORT]` swallowed the first one:
    `ronin --watch fix the test` refused with "invalid int value: 'fix'" — on the most
    natural way anyone would type it.
    """
    parsed = parse(["--watch", "fix", "the", "test"])
    assert isinstance(parsed, Options)
    assert parsed.prompt == "fix the test"


def test_the_port_a_tunnel_already_forwards_can_be_named() -> None:
    parsed = parse(["--watch-port", "8900", "go"])
    assert isinstance(parsed, Options)
    assert parsed.watch_port == 8900


def test_naming_a_port_is_asking_for_the_stream() -> None:
    """Requiring both flags only ever produces a run with a port and no watcher."""
    parsed = parse(["--watch-port", "8900"])
    assert isinstance(parsed, Options)
    assert parsed.watch_port == 8900


def test_no_flag_means_no_socket_is_opened_at_all() -> None:
    parsed = parse(["do the thing"])
    assert isinstance(parsed, Options)
    assert parsed.watch_port is None


@pytest.mark.parametrize("port", ["65536", "-1", "99999"])
def test_a_port_that_is_not_a_port_is_refused(port: str) -> None:
    parsed = parse(["--watch-port", port])
    assert isinstance(parsed, Usage)
    assert "between 0 and 65535" in parsed.message
    assert "--watch-port" in parsed.message


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
