"""Slack: an app mention becomes a summons, an answer becomes a threaded reply.

``docs/RETAINER.md`` §8 step 7b, and the test of whether the core is really
platform-agnostic or only claims to be. Three things about Slack differ from
GitHub in kind rather than in spelling, and each one is a place the shared
shapes either held or would have had to bend:

**A mention is an id, not a name.** Slack rewrites ``@ronin`` into
``<@U07RONIN>`` before the event ever leaves the client, so there is no handle to
search for — there is a *user id* to match exactly. Nothing downstream noticed,
because :class:`~ronin.retainer.model.Summons` was never told what a handle is.

**A thread key has two parts.** A Slack conversation is
``channel + thread_ts``, and a message that is not in a thread is its own root.
That is still one opaque string to everything above the adapter, which is why
``Summons.thread`` is typed as one.

**Not every delivery is a summons.** Slack's URL-verification handshake is a
challenge to echo, and a retry is the same event arriving again with a header
saying so. Both are answered here rather than being turned into a Summons that
the plane above would have to learn to ignore.

The retry header is worth naming: Slack redelivers when it does not get a 200
within three seconds, which is exactly the condition a long-running agent
creates. :func:`is_retry` exists so a receiver can tell a redelivery from a new
message — though the effect ledger is what actually makes it harmless, since a
receiver that merely *tries* to notice duplicates will eventually miss one.

Nothing here opens a socket. Posting is injected, as with every adapter.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from ronin.retainer.adapters.common import strip_quotes_and_code
from ronin.retainer.model import Channel, Summons, SummonsKind

#: The event types worth waking a Retainer for. ``app_mention`` is the one Slack
#: sends when the bot is addressed; ``message`` is included so a reply inside a
#: thread the Retainer is already in counts without re-addressing it every time.
WAKING_TYPES: Final = frozenset({"app_mention", "message"})

#: Message subtypes that are not somebody talking: joins, topic changes, edits
#: of other people's messages, and — the one that matters — a bot's own post.
IGNORED_SUBTYPES: Final = frozenset(
    {"bot_message", "channel_join", "channel_leave", "message_changed", "message_deleted"}
)

#: Slack's header on a redelivery. Present from the first retry onwards.
RETRY_HEADER: Final = "X-Slack-Retry-Num"

_LINKED_USER = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")


@dataclass(frozen=True, slots=True)
class SlackMention:
    """One delivery, understood."""

    team: str
    channel: str
    thread_ts: str
    ts: str
    actor: str
    """The Slack **user id**, which is what an authority check must use."""
    text: str
    """What was said, with the bot's own link removed. Untrusted content."""

    @property
    def thread(self) -> str:
        """The conversation key: ``channel/thread_ts``, stable for every reply."""
        return f"{self.channel}/{self.thread_ts}"


def challenge_of(body: Mapping[str, Any]) -> str:
    """Slack's URL-verification challenge, or empty if this is not one.

    Answered here because it is a fact about the wire, not about any Retainer:
    the plane above should never see a delivery it has to know to ignore.
    """
    if str(body.get("type", "")) != "url_verification":
        return ""
    challenge = body.get("challenge")
    return str(challenge) if isinstance(challenge, str) else ""


def is_retry(headers: Mapping[str, str]) -> bool:
    """Whether Slack is redelivering. Informational — the ledger is the guard."""
    for key, value in headers.items():
        if key.lower() == RETRY_HEADER.lower():
            return bool(value)
    return False


def addressed(text: str, bot_user: str) -> bool:
    """Whether ``<@bot_user>`` appears in prose rather than in quoted or code text."""
    if not bot_user:
        return False
    speech = strip_quotes_and_code(text)
    return any(found == bot_user for found in _LINKED_USER.findall(speech))


def without_link(text: str, bot_user: str) -> str:
    """The message with the bot's own link removed, so the prompt is the request."""
    if not bot_user:
        return text.strip()
    return re.sub(rf"<@{re.escape(bot_user)}(?:\|[^>]*)?>", "", text).strip()


def read_mention(
    body: Mapping[str, Any],
    *,
    bot_user: str,
    known_threads: frozenset[str] = frozenset(),
) -> SlackMention | None:
    """Understand one Events API delivery, or ``None`` if it is not for us.

    ``known_threads`` lets a reply inside a conversation the Retainer is already
    holding count without being re-addressed, which is how people actually talk.
    Empty by default, so the permissive behaviour is opt-in rather than the
    thing that happens to whoever forgets the argument.
    """
    if str(body.get("type", "")) != "event_callback":
        return None
    event = body.get("event")
    if not isinstance(event, Mapping):
        return None
    if str(event.get("type", "")) not in WAKING_TYPES:
        return None
    if str(event.get("subtype", "")) in IGNORED_SUBTYPES:
        return None
    # A bot's own post carries bot_id even where the subtype does not say so.
    if event.get("bot_id"):
        return None

    actor = str(event.get("user", ""))
    if not actor or (bot_user and actor == bot_user):
        return None

    channel = str(event.get("channel", ""))
    ts = str(event.get("ts", ""))
    if not channel or not ts:
        return None
    thread_ts = str(event.get("thread_ts") or ts)

    text = str(event.get("text", ""))
    if not addressed(text, bot_user) and f"{channel}/{thread_ts}" not in known_threads:
        return None

    return SlackMention(
        team=str(body.get("team_id", "")),
        channel=channel,
        thread_ts=thread_ts,
        ts=ts,
        actor=actor,
        text=without_link(text, bot_user),
    )


def to_summons(mention: SlackMention, retainer: str) -> Summons:
    """The normalised request. Downstream stops knowing this was Slack."""
    return Summons(
        retainer=retainer,
        kind=SummonsKind.MENTION,
        channel=Channel.SLACK,
        thread=mention.thread,
        text=mention.text,
        actor=mention.actor,
    )


def escalation_summons(mention: SlackMention, retainer: str, escalation: str) -> Summons:
    """A reply that answers an escalation rather than asking for something new."""
    return Summons(
        retainer=retainer,
        kind=SummonsKind.REPLY,
        channel=Channel.SLACK,
        thread=mention.thread,
        text=mention.text,
        actor=mention.actor,
        escalation=escalation,
    )


#: Slack's mrkdwn, not Markdown: single asterisks bold, and the instructions go
#: in backticks for the same reason GitHub's do — :func:`addressed` and
#: ``read_answer`` strip code, so this message cannot wake or answer itself.
ESCALATION_TEMPLATE: Final = """*{retainer} needs an approval to continue.*

It wants to run `{tool}`:
```
{rendered}
```
Reply in this thread with `allow {escalation}` or `deny {escalation}`.

Anyone who can post here may answer. Approving permits the action above and \
nothing else — it does not undo anything already done."""


def ask_body(*, retainer: str, tool: str, rendered: str, escalation: str) -> str:
    """The message that puts an escalation to the thread."""
    return ESCALATION_TEMPLATE.format(
        retainer=retainer, tool=tool, rendered=rendered.strip(), escalation=escalation
    )


__all__ = [
    "ESCALATION_TEMPLATE",
    "IGNORED_SUBTYPES",
    "RETRY_HEADER",
    "WAKING_TYPES",
    "SlackMention",
    "addressed",
    "ask_body",
    "challenge_of",
    "escalation_summons",
    "is_retry",
    "read_mention",
    "to_summons",
    "without_link",
]
