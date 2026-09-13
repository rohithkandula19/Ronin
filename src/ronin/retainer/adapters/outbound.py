"""The reply leaving the machine — the one place in this package that opens a socket.

Everything else in :mod:`ronin.retainer` is deliberately socket-free: the
adapters understand wire *formats*, ``run_summons`` takes an injected
:data:`~ronin.cli.retainer_run.Poster`, and the tests run offline. That split is
what let the plane be built and proven before anything could be sent. This module
is the other half, and it is small on purpose.

Two halves again, for the same reason
-------------------------------------
:func:`reply_request` turns a :class:`~ronin.retainer.model.Summons` and a body
into a :class:`Request` and touches no network. :func:`send` performs one. The
suite exercises the first exhaustively and the second through an injected opener,
so "what would have been sent to GitHub" is an assertion rather than a capture.

Credentials, and the gap this leaves
------------------------------------
Tokens are read from the environment at post time and never from the registry,
because a config describing a fleet is a file people paste into issues. That is
the best available answer and it is **not** the designed one: ``docs/RETAINER.md``
§6.7 puts an egress credential proxy here, so the Retainer would hold no token at
all and revoking one would not mean editing a machine's environment. The proxy is
not built. Until it is, a Retainer holds the credential it posts with, and a
compromised post is a compromised token.

Where the thread key comes from
-------------------------------
``Summons.thread`` is derived from a webhook body, which is attacker-influenced
even after the signature check — a valid signature proves the platform sent it,
not that its contents are tame. So every key is matched against a strict pattern
before it becomes part of a URL. An unvalidated ``owner/repo#number`` is a path
traversal with a friendly name.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from ronin.retainer.model import Channel, Summons

#: How long one post may take. A Retainer that hangs on a reply holds its whole
#: run open, and the run is holding a workspace.
DEFAULT_TIMEOUT_SECONDS: Final = 15.0

#: Bound on a response we are only reading an id out of. A platform that answers
#: with a hundred megabytes is a platform having a bad day, not a reply.
MAX_RESPONSE_BYTES: Final = 1 << 20

#: The one host each channel may be posted to. Not configurable: the point of a
#: fixed table is that no payload can move the destination.
HOSTS: Final[Mapping[Channel, str]] = {
    Channel.GITHUB: "api.github.com",
    Channel.SLACK: "slack.com",
    Channel.TELEGRAM: "api.telegram.org",
}

#: Environment variables consulted per channel, in order. GitHub gets two because
#: both spellings are in wide use and picking one means half of everyone is wrong.
TOKEN_VARIABLES: Final[Mapping[Channel, tuple[str, ...]]] = {
    Channel.GITHUB: ("GITHUB_TOKEN", "GH_TOKEN"),
    Channel.SLACK: ("SLACK_BOT_TOKEN",),
    Channel.TELEGRAM: ("TELEGRAM_BOT_TOKEN",),
}

#: ``owner/repo#number``. Anchored, and no path separators inside the segments,
#: because this string is interpolated into a URL path.
GITHUB_THREAD: Final = re.compile(r"\A([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)#([0-9]{1,12})\Z")

#: ``channel/thread_ts``. Slack ids are alphanumeric; the timestamp is ``1234.5678``.
SLACK_THREAD: Final = re.compile(r"\A([A-Z0-9]+)/([0-9]+\.[0-9]+)\Z")

#: ``chat`` or ``chat/topic``. Chat ids are numeric and may be negative for groups.
TELEGRAM_THREAD: Final = re.compile(r"\A(-?[0-9]{1,20})(?:/([0-9]{1,20}))?\Z")


class PostFailed(RuntimeError):
    """A reply could not be delivered. The message is safe to log."""


@dataclass(frozen=True, slots=True)
class Request:
    """One outbound call, fully formed and not yet made."""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def __post_init__(self) -> None:
        if not self.url.startswith("https://"):
            # Plaintext would put a bearer token on the wire. There is no
            # deployment where that is the right trade, so it is not an option.
            raise PostFailed(f"refusing a non-HTTPS post to {redact(self.url)}")


def redact(text: str, *secrets: str) -> str:
    """``text`` with credentials removed, for logs and error messages.

    Telegram puts the bot token **in the path** — ``/bot<token>/sendMessage`` —
    so a URL in an exception is a leaked credential. Any explicitly named secret
    is scrubbed too, since a 401 body can echo the header it rejected.
    """
    out = re.sub(r"/bot[^/]+/", "/bot<redacted>/", text)
    for secret in secrets:
        if secret:
            out = out.replace(secret, "<redacted>")
    return out


def token_for(channel: Channel, environ: Mapping[str, str]) -> str:
    """The credential for ``channel``, or a refusal naming what to set."""
    names = TOKEN_VARIABLES[channel]
    for name in names:
        value = environ.get(name, "").strip()
        if value:
            return value
    offered = " or ".join(names)
    raise PostFailed(f"no {channel.value} credential — set {offered}")


def _match(pattern: re.Pattern[str], thread: str, *, channel: Channel) -> re.Match[str]:
    found = pattern.match(thread)
    if found is None:
        # The key is quoted so a reader can see the whitespace or separator that
        # actually broke it, which is most of these.
        raise PostFailed(f"{thread!r} is not a {channel.value} thread key")
    return found


def _json_request(method: str, url: str, payload: Mapping[str, Any], **headers: str) -> Request:
    body = json.dumps(payload).encode("utf-8")
    return Request(
        method=method,
        url=url,
        headers={"content-type": "application/json", **headers},
        body=body,
    )


def reply_request(summons: Summons, text: str, *, token: str) -> Request:
    """The call that posts ``text`` into the conversation ``summons`` came from.

    Pure. The whole outbound surface is three of these, and every one is a
    function of the summons alone — which is a property of the design rather than
    a coincidence: each adapter already folds everything a reply needs into
    ``Summons.thread``.
    """
    if not text.strip():
        # An empty comment is a notification with nothing in it, and the ledger
        # would record it as a delivered reply.
        raise PostFailed("refusing to post an empty reply")
    channel = summons.channel
    if channel is Channel.GITHUB:
        found = _match(GITHUB_THREAD, summons.thread, channel=channel)
        owner, repo, number = found.groups()
        return _json_request(
            "POST",
            f"https://{HOSTS[channel]}/repos/{owner}/{repo}/issues/{number}/comments",
            {"body": text},
            authorization=f"Bearer {token}",
            accept="application/vnd.github+json",
            **{"x-github-api-version": "2022-11-28"},
        )
    if channel is Channel.SLACK:
        found = _match(SLACK_THREAD, summons.thread, channel=channel)
        conversation, thread_ts = found.groups()
        return _json_request(
            "POST",
            f"https://{HOSTS[channel]}/api/chat.postMessage",
            {"channel": conversation, "thread_ts": thread_ts, "text": text},
            authorization=f"Bearer {token}",
        )
    found = _match(TELEGRAM_THREAD, summons.thread, channel=channel)
    chat, topic = found.groups()
    payload: dict[str, Any] = {"chat_id": chat, "text": text}
    if topic:
        payload["message_thread_id"] = topic
    # The token is in the path here, not a header. `redact` exists for this.
    return _json_request("POST", f"https://{HOSTS[channel]}/bot{token}/sendMessage", payload)


def posted_id(channel: Channel, payload: object) -> str:
    """The platform's id for what was just posted, for the effect ledger.

    Returning ``""`` rather than raising when the shape is unfamiliar: the reply
    *was* delivered, and failing here would have the ledger record a success as a
    failure and the next delivery post it twice.
    """
    if not isinstance(payload, Mapping):
        return ""
    if channel is Channel.GITHUB:
        value = payload.get("id")
        return str(value) if value is not None else ""
    if channel is Channel.SLACK:
        value = payload.get("ts")
        return str(value) if isinstance(value, str) else ""
    result = payload.get("result")
    if isinstance(result, Mapping):
        value = result.get("message_id")
        return str(value) if value is not None else ""
    return ""


def check_response(channel: Channel, raw: bytes) -> object:
    """The decoded body, refusing a failure the transport called a success.

    Slack answers ``200 OK`` with ``{"ok": false, "error": "not_in_channel"}``.
    A poster that only reads the status code records that as a delivered reply,
    and the ledger then suppresses every retry of a message nobody ever saw.
    """
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, Mapping):
        if channel is Channel.SLACK and payload.get("ok") is False:
            raise PostFailed(f"slack refused the post: {payload.get('error', 'unknown')}")
        if channel is Channel.TELEGRAM and payload.get("ok") is False:
            raise PostFailed(f"telegram refused the post: {payload.get('description', 'unknown')}")
    return payload


#: How a formed request actually travels. Injected so the suite never opens one.
Opener = Callable[[Request, float], bytes]


def _urllib_opener(request: Request, timeout: float) -> bytes:
    built = urllib.request.Request(
        request.url,
        data=request.body or None,
        headers=dict(request.headers),
        method=request.method,
    )
    with urllib.request.urlopen(built, timeout=timeout) as response:
        return bytes(response.read(MAX_RESPONSE_BYTES))


async def send(
    request: Request,
    *,
    channel: Channel,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Opener = _urllib_opener,
    secret: str = "",
) -> str:
    """Perform ``request`` and return the platform's id for what was posted.

    ``urllib`` is synchronous, so it runs on a thread — the same shape
    ``tools/fetcher.py`` uses, for the same reason.
    """
    try:
        raw = await asyncio.to_thread(opener, request, timeout)
    except urllib.error.HTTPError as error:
        detail = error.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace").strip()
        raise PostFailed(
            f"{channel.value} refused the post: {error.code} {redact(detail, secret)}"
        ) from error
    except (OSError, urllib.error.URLError) as error:
        raise PostFailed(
            f"could not reach {channel.value}: {redact(str(error), secret)}"
        ) from error
    return posted_id(channel, check_response(channel, raw))


def http_poster(
    *,
    environ: Mapping[str, str],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Opener = _urllib_opener,
) -> Callable[[Summons, str], Any]:
    """A :data:`~ronin.cli.retainer_run.Poster` that really posts.

    The credential is resolved per call rather than captured once, so rotating a
    token in the environment of a restarted daemon does not need this object
    rebuilt, and a channel nobody has a token for fails only when it is used.
    """

    async def post(summons: Summons, text: str) -> str:
        token = token_for(summons.channel, environ)
        request = reply_request(summons, text, token=token)
        return await send(
            request, channel=summons.channel, timeout=timeout, opener=opener, secret=token
        )

    return post


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "GITHUB_THREAD",
    "HOSTS",
    "MAX_RESPONSE_BYTES",
    "SLACK_THREAD",
    "TELEGRAM_THREAD",
    "TOKEN_VARIABLES",
    "Opener",
    "PostFailed",
    "Request",
    "check_response",
    "http_poster",
    "posted_id",
    "redact",
    "reply_request",
    "send",
    "token_for",
]
