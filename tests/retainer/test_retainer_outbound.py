"""The reply leaving the machine, proven without a socket.

`reply_request` is pure, so "what would have been sent to GitHub" is an assertion
rather than a packet capture. `send` takes an injected opener, so the delivery
path — including every failure of it — runs offline like the rest of the suite.

The tests that matter most here are the refusals. A poster is the one component
that holds a credential and builds a URL out of webhook content, so the
interesting questions are all "what happens when the input is hostile or the
platform lies", not "does the happy path work".
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from ronin.retainer.adapters.outbound import (
    HOSTS,
    TOKEN_VARIABLES,
    Opener,
    PostFailed,
    Request,
    check_response,
    http_poster,
    posted_id,
    redact,
    reply_request,
    send,
    token_for,
)
from ronin.retainer.model import Channel, Summons, SummonsKind

TOKEN = "xoxb-super-secret-value"

ENVIRON = {
    "GITHUB_TOKEN": "ghp_secret",
    "SLACK_BOT_TOKEN": "xoxb-secret",
    "TELEGRAM_BOT_TOKEN": "123:ABC-secret",
}

THREADS = {
    Channel.GITHUB: "rohithkandula19/Ronin#258",
    Channel.SLACK: "C01ABCDEF/1712345678.000100",
    Channel.TELEGRAM: "-1001234567890",
}


def summons(channel: Channel, *, thread: str | None = None) -> Summons:
    return Summons(
        retainer="ci-keeper",
        kind=SummonsKind.MENTION,
        channel=channel,
        thread=THREADS[channel] if thread is None else thread,
        text="please look",
        actor="someone",
    )


def body_of(request: Request) -> Mapping[str, Any]:
    decoded = json.loads(request.body.decode("utf-8"))
    assert isinstance(decoded, Mapping)
    return decoded


def opener_returning(payload: object, *, seen: list[Request] | None = None) -> Opener:
    def opener(request: Request, timeout: float) -> bytes:
        if seen is not None:
            seen.append(request)
        return json.dumps(payload).encode("utf-8")

    return opener


# --------------------------------------------------------------------------- #
# What gets built
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("channel", list(Channel))
def test_every_channel_posts_over_https_to_its_own_host(channel: Channel) -> None:
    """A fixed host table, so no payload can move the destination."""
    request = reply_request(summons(channel), "done", token=TOKEN)
    assert request.url.startswith(f"https://{HOSTS[channel]}/")
    assert request.method == "POST"


def test_a_github_reply_addresses_the_issue_the_mention_came_from() -> None:
    request = reply_request(summons(Channel.GITHUB), "done", token=TOKEN)
    assert request.url.endswith("/repos/rohithkandula19/Ronin/issues/258/comments")
    assert body_of(request) == {"body": "done"}
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


def test_a_slack_reply_lands_in_the_thread_rather_than_the_channel() -> None:
    """Without `thread_ts` the answer appears at the bottom of the channel,
    detached from the question that prompted it."""
    request = reply_request(summons(Channel.SLACK), "done", token=TOKEN)
    assert body_of(request) == {
        "channel": "C01ABCDEF",
        "thread_ts": "1712345678.000100",
        "text": "done",
    }


def test_a_telegram_reply_carries_the_topic_only_when_there_is_one() -> None:
    plain = body_of(reply_request(summons(Channel.TELEGRAM), "done", token=TOKEN))
    assert plain == {"chat_id": "-1001234567890", "text": "done"}

    topical = body_of(
        reply_request(summons(Channel.TELEGRAM, thread="-100123/77"), "done", token=TOKEN)
    )
    assert topical["message_thread_id"] == "77"


def test_the_telegram_token_goes_in_the_path_where_telegram_wants_it() -> None:
    request = reply_request(summons(Channel.TELEGRAM), "done", token=TOKEN)
    assert f"/bot{TOKEN}/sendMessage" in request.url


# --------------------------------------------------------------------------- #
# Hostile input
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("channel", "thread"),
    [
        (Channel.GITHUB, "owner/repo#1/../../../secrets"),
        (Channel.GITHUB, "owner/../../repo#1"),
        (Channel.GITHUB, "owner/repo#notanumber"),
        (Channel.GITHUB, "https://evil.test/owner/repo#1"),
        (Channel.GITHUB, "owner/repo#1 extra"),
        (Channel.SLACK, "C01ABCDEF/../../x"),
        (Channel.SLACK, "C01ABCDEF"),
        (Channel.TELEGRAM, "123/../x"),
        (Channel.TELEGRAM, "not-a-chat"),
    ],
)
def test_a_thread_key_that_is_not_one_never_becomes_a_url(channel: Channel, thread: str) -> None:
    """`Summons.thread` comes from a webhook body. A valid signature proves the
    platform sent it, not that its contents are tame — an unvalidated
    `owner/repo#number` is a path traversal with a friendly name."""
    with pytest.raises(PostFailed, match="thread key"):
        reply_request(summons(channel, thread=thread), "done", token=TOKEN)


def test_an_empty_thread_never_reaches_the_poster_at_all() -> None:
    """The one hostile key the poster does not have to defend against: `Summons`
    refuses it at construction, so there is no path from a webhook to a URL with
    an empty thread in it. Pinned where the invariant lives rather than asserted
    twice."""
    with pytest.raises(ValueError, match="thread is required"):
        summons(Channel.GITHUB, thread="")


def test_an_empty_reply_is_refused_rather_than_posted() -> None:
    """An empty comment is a notification with nothing in it, and the ledger
    would record it as a delivered reply and suppress the real one."""
    with pytest.raises(PostFailed, match="empty reply"):
        reply_request(summons(Channel.GITHUB), "   \n  ", token=TOKEN)


def test_a_non_https_request_cannot_be_constructed() -> None:
    """Plaintext would put a bearer token on the wire."""
    with pytest.raises(PostFailed, match="non-HTTPS"):
        Request(method="POST", url="http://api.github.com/x")


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("channel", list(Channel))
def test_a_missing_credential_names_the_variable_to_set(channel: Channel) -> None:
    with pytest.raises(PostFailed, match="set ") as caught:
        token_for(channel, {})
    for name in TOKEN_VARIABLES[channel]:
        assert name in str(caught.value)


def test_github_accepts_either_spelling_of_its_token() -> None:
    """Both are in wide use; picking one means half of everyone is wrong."""
    assert token_for(Channel.GITHUB, {"GITHUB_TOKEN": "a"}) == "a"
    assert token_for(Channel.GITHUB, {"GH_TOKEN": "b"}) == "b"
    assert token_for(Channel.GITHUB, {"GITHUB_TOKEN": "a", "GH_TOKEN": "b"}) == "a"


def test_a_blank_credential_counts_as_missing() -> None:
    """An exported-but-empty variable is the usual shape of a broken deployment,
    and `Bearer ` with nothing after it is a 401 nobody can read."""
    with pytest.raises(PostFailed, match="no github credential"):
        token_for(Channel.GITHUB, {"GITHUB_TOKEN": "   "})


def test_a_telegram_url_never_appears_in_a_message_with_its_token() -> None:
    """Telegram puts the bot token in the path, so a URL in an exception is a
    leaked credential."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert TOKEN not in redact(url)
    assert "<redacted>" in redact(url)


def test_an_explicitly_named_secret_is_scrubbed_too() -> None:
    """A 401 body can echo the header it rejected."""
    assert TOKEN not in redact(f"unauthorized: {TOKEN}", TOKEN)


def test_redaction_of_nothing_is_not_a_crash() -> None:
    assert redact("plain text", "") == "plain text"


# --------------------------------------------------------------------------- #
# What comes back
# --------------------------------------------------------------------------- #


def test_slack_saying_ok_false_with_a_200_is_a_failure() -> None:
    """The footgun this function exists for. Slack answers 200 OK with
    `{"ok": false}`; a poster reading only the status records a delivered reply,
    and the ledger then suppresses every retry of a message nobody ever saw."""
    raw = json.dumps({"ok": False, "error": "not_in_channel"}).encode()
    with pytest.raises(PostFailed, match="not_in_channel"):
        check_response(Channel.SLACK, raw)


def test_telegram_saying_ok_false_is_a_failure_too() -> None:
    raw = json.dumps({"ok": False, "description": "chat not found"}).encode()
    with pytest.raises(PostFailed, match="chat not found"):
        check_response(Channel.TELEGRAM, raw)


def test_a_success_body_is_returned_for_its_id() -> None:
    assert check_response(Channel.SLACK, json.dumps({"ok": True, "ts": "1.2"}).encode()) == {
        "ok": True,
        "ts": "1.2",
    }


@pytest.mark.parametrize(
    ("channel", "payload", "expected"),
    [
        (Channel.GITHUB, {"id": 12345}, "12345"),
        (Channel.SLACK, {"ts": "1712345678.000200"}, "1712345678.000200"),
        (Channel.TELEGRAM, {"result": {"message_id": 99}}, "99"),
    ],
)
def test_the_posted_id_is_read_for_the_ledger(
    channel: Channel, payload: object, expected: str
) -> None:
    assert posted_id(channel, payload) == expected


@pytest.mark.parametrize("payload", [{}, {"unexpected": 1}, "not a mapping", None])
def test_an_unfamiliar_success_shape_yields_no_id_rather_than_an_error(payload: object) -> None:
    """The reply *was* delivered. Raising here would have the ledger record a
    success as a failure, and the next delivery would post it twice."""
    assert posted_id(Channel.GITHUB, payload) == ""


@pytest.mark.parametrize("raw", [b"", b"   ", b"<html>not json</html>"])
def test_a_body_that_is_not_json_is_not_a_failure(raw: bytes) -> None:
    assert check_response(Channel.GITHUB, raw) == {}


# --------------------------------------------------------------------------- #
# Sending, with the socket injected
# --------------------------------------------------------------------------- #


async def test_a_post_returns_the_platforms_id() -> None:
    seen: list[Request] = []
    request = reply_request(summons(Channel.GITHUB), "done", token=TOKEN)
    got = await send(
        request,
        channel=Channel.GITHUB,
        opener=opener_returning({"id": 7}, seen=seen),
    )
    assert got == "7"
    assert seen == [request], "the request performed is the request built"


async def test_a_transport_failure_is_reported_without_the_credential() -> None:
    def explode(request: Request, timeout: float) -> bytes:
        raise OSError(f"connection refused while sending {TOKEN}")

    with pytest.raises(PostFailed) as caught:
        await send(
            reply_request(summons(Channel.TELEGRAM), "done", token=TOKEN),
            channel=Channel.TELEGRAM,
            opener=explode,
            secret=TOKEN,
        )
    assert TOKEN not in str(caught.value)
    assert "could not reach telegram" in str(caught.value)


async def test_the_whole_poster_resolves_its_credential_per_call() -> None:
    """Resolved per call rather than captured once, so a rotated token in a
    restarted daemon's environment does not need this object rebuilt."""
    seen: list[Request] = []
    post = http_poster(environ=ENVIRON, opener=opener_returning({"id": 1}, seen=seen))
    assert await post(summons(Channel.GITHUB), "done") == "1"
    assert seen[0].headers["authorization"] == "Bearer ghp_secret"


async def test_a_poster_fails_only_on_the_channel_it_has_no_token_for() -> None:
    """A deployment with one platform configured must still serve that one."""
    post = http_poster(environ={"GITHUB_TOKEN": "ghp_secret"}, opener=opener_returning({"id": 1}))
    assert await post(summons(Channel.GITHUB), "done") == "1"
    with pytest.raises(PostFailed, match="SLACK_BOT_TOKEN"):
        await post(summons(Channel.SLACK), "done")
