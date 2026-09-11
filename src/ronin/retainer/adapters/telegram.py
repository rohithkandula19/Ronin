"""Telegram: a message becomes a summons, an answer becomes a threaded reply.

``docs/RETAINER.md`` §8 step 7c. The third adapter, and the one that says
whether the abstraction was real. The answer: it added **nothing** to the shared
types — no new field on ``Summons``, no new ``Channel`` behaviour, no change to
the answer parser — despite differing from the other two in every surface
detail below.

Telegram differs from the other two in three ways that matter, and one that
looks like it should and does not.

**A mention is a name again, but with a suffix.** In a group, addressing a bot
is ``/ask@ronin_bot`` or ``@ronin_bot``; the same command in a private chat is
just ``/ask``. So a private chat needs no address at all — the whole
conversation is with the bot — while a group does. That is one flag, not two
code paths.

**Entities, not parsing.** Telegram sends the offsets of every mention and code
span it found, in ``entities``. Where they are present they are authoritative
and are used, because Telegram's own idea of where a code span ends is the one
its clients rendered. Where they are absent — an edited message, a client that
omits them — the shared text stripping is the fallback.

**A thread is a chat, or a topic inside one.** ``message_thread_id`` exists only
in forum groups, so the key is ``chat_id`` or ``chat_id/topic``, and everything
above the adapter still sees one string.

**And the thing that looks different but is not:** Telegram authenticates with a
shared secret token rather than a signature, which is entirely handled in
``cli/retain.py`` by :data:`~ronin.cli.retain.TELEGRAM_SCHEME`. Nothing about it
reaches this file.

There is a v1 ``telegram_bot.py`` under ``packages/cli``. It belongs to the v1
tree and is not reused; this is not a port of it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from ronin.retainer.adapters.common import strip_quotes_and_code
from ronin.retainer.model import Channel, Summons, SummonsKind

#: The update fields worth waking a Retainer for. Edits count because a person
#: fixing a typo in the message that addressed the bot still means it.
WAKING_FIELDS: Final = ("message", "edited_message")

#: Chat types where every message is addressed to the bot by construction.
PRIVATE_TYPES: Final = frozenset({"private"})

#: Entity types Telegram uses for text that is code. Kept as a set because a
#: mention inside any of them is documentation, not an address.
CODE_ENTITIES: Final = frozenset({"code", "pre"})

#: Entity types that can carry an address. ``bot_command`` is here because
#: ``/ask@ronin_bot`` — the canonical way to address one bot in a group holding
#: several — arrives as a command, not as a mention.
ADDRESS_ENTITIES: Final = frozenset({"mention", "bot_command"})


@dataclass(frozen=True, slots=True)
class TelegramMention:
    """One update, understood."""

    chat: str
    topic: str
    message_id: str
    actor: str
    """The sender's numeric Telegram id as text — stable, unlike a username."""
    text: str
    """What was said, with the bot's own handle removed. Untrusted content."""
    private: bool = False

    @property
    def thread(self) -> str:
        """``chat`` or ``chat/topic``. One opaque string to everything above."""
        return f"{self.chat}/{self.topic}" if self.topic else self.chat


def _spans(entities: object) -> tuple[tuple[int, int], ...]:
    """The offsets of every code entity Telegram reported, if it reported any."""
    if not isinstance(entities, Sequence) or isinstance(entities, (str, bytes)):
        return ()
    found: list[tuple[int, int]] = []
    for entity in entities:
        if not isinstance(entity, Mapping):
            continue
        if str(entity.get("type", "")) not in CODE_ENTITIES:
            continue
        offset, length = entity.get("offset"), entity.get("length")
        if isinstance(offset, int) and isinstance(length, int) and length > 0:
            found.append((offset, offset + length))
    return tuple(found)


def speech(text: str, entities: object) -> str:
    """The parts of ``text`` that are prose, by Telegram's own reckoning.

    Telegram's offsets win where it sent them, because its clients rendered the
    message using exactly those spans — reparsing would be a second opinion
    about where a code block ends. Where it sent none, the shared stripping is
    the fallback rather than trusting the raw string.
    """
    spans = _spans(entities)
    if not spans:
        return strip_quotes_and_code(text)
    kept = list(text)
    for start, end in spans:
        for index in range(max(0, start), min(len(kept), end)):
            kept[index] = " "
    return strip_quotes_and_code("".join(kept))


def _addresses(text: str, entities: object) -> tuple[str, ...]:
    """The address-bearing spans Telegram itself identified, verbatim."""
    if not isinstance(entities, Sequence) or isinstance(entities, (str, bytes)):
        return ()
    found: list[str] = []
    for entity in entities:
        if not isinstance(entity, Mapping):
            continue
        if str(entity.get("type", "")) not in ADDRESS_ENTITIES:
            continue
        offset, length = entity.get("offset"), entity.get("length")
        if isinstance(offset, int) and isinstance(length, int) and length > 0:
            found.append(text[max(0, offset) : offset + length])
    return tuple(found)


def addressed(text: str, entities: object, handle: str, *, private: bool) -> bool:
    """Whether the bot was addressed. In a private chat it always was.

    Where Telegram reported entities, they decide: a ``mention`` or a
    ``bot_command`` span ending in ``@handle`` is an address, and one inside a
    code span was already blanked by :func:`speech`. Where it reported none, the
    regex below is the fallback.

    The fallback deliberately allows a character before the ``@``, unlike the
    GitHub adapter's: ``/ask@ronin_bot`` is *the* way to address one bot in a
    group holding several, and rejecting it — as a lookbehind borrowed from
    GitHub does — makes the adapter deaf to its most common input. What it must
    still reject is an email-shaped string, so a domain-looking ``.`` after the
    handle disqualifies the match.
    """
    if private:
        return True
    if not handle:
        return False
    prose = speech(text, entities)
    wanted = f"@{handle}".lower()
    for span in _addresses(text, entities):
        if span.lower().endswith(wanted) and span in prose:
            return True
    pattern = rf"(?<!@)@{re.escape(handle)}(?![A-Za-z0-9_])(?!\.[A-Za-z])"
    return re.search(pattern, prose, re.IGNORECASE) is not None


def without_handle(text: str, handle: str) -> str:
    """The message with the bot's handle removed, including a ``/cmd@handle`` form."""
    if not handle:
        return text.strip()
    return re.sub(rf"@{re.escape(handle)}", "", text, flags=re.IGNORECASE).strip()


def read_mention(
    update: Mapping[str, Any],
    *,
    handle: str,
    ourselves: str = "",
) -> TelegramMention | None:
    """Understand one update, or ``None`` if it is not for us."""
    message: Mapping[str, Any] | None = None
    for field in WAKING_FIELDS:
        candidate = update.get(field)
        if isinstance(candidate, Mapping):
            message = candidate
            break
    if message is None:
        return None

    sender = message.get("from")
    if not isinstance(sender, Mapping):
        return None
    if sender.get("is_bot") is True:
        # Any bot, including us. Telegram groups routinely contain several, and
        # a Retainer that answers one is a Retainer in a loop with it.
        return None
    actor = str(sender.get("id", ""))
    if not actor or (ourselves and actor == ourselves):
        return None

    chat = message.get("chat")
    if not isinstance(chat, Mapping) or chat.get("id") is None:
        return None
    private = str(chat.get("type", "")) in PRIVATE_TYPES

    text = str(message.get("text") or message.get("caption") or "")
    entities = message.get("entities") or message.get("caption_entities")
    if not addressed(text, entities, handle, private=private):
        return None

    topic = message.get("message_thread_id")
    return TelegramMention(
        chat=str(chat["id"]),
        topic=str(topic) if topic is not None else "",
        message_id=str(message.get("message_id", "")),
        actor=actor,
        text=without_handle(text, handle),
        private=private,
    )


def to_summons(mention: TelegramMention, retainer: str) -> Summons:
    """The normalised request. Downstream stops knowing this was Telegram."""
    return Summons(
        retainer=retainer,
        kind=SummonsKind.MENTION,
        channel=Channel.TELEGRAM,
        thread=mention.thread,
        text=mention.text,
        actor=mention.actor,
    )


def escalation_summons(mention: TelegramMention, retainer: str, escalation: str) -> Summons:
    """A reply that answers an escalation rather than asking for something new."""
    return Summons(
        retainer=retainer,
        kind=SummonsKind.REPLY,
        channel=Channel.TELEGRAM,
        thread=mention.thread,
        text=mention.text,
        actor=mention.actor,
        escalation=escalation,
    )


#: MarkdownV2 would need every ``.``, ``-`` and ``!`` escaped, and an escape
#: missed anywhere makes Telegram reject the whole message rather than render it
#: oddly. Plain text with backticks sends reliably, and the instructions are
#: still in code so the shared parser strips them — the same protection the other
#: two adapters get, by the same mechanism.
ESCALATION_TEMPLATE: Final = """{retainer} needs an approval to continue.

It wants to run `{tool}`:

`{rendered}`

Reply here with `allow {escalation}` or `deny {escalation}`.

Anyone in this chat may answer. Approving permits the action above and nothing \
else — it does not undo anything already done."""


def ask_body(*, retainer: str, tool: str, rendered: str, escalation: str) -> str:
    """The message that puts an escalation to the chat."""
    return ESCALATION_TEMPLATE.format(
        retainer=retainer, tool=tool, rendered=rendered.strip(), escalation=escalation
    )


__all__ = [
    "ADDRESS_ENTITIES",
    "CODE_ENTITIES",
    "ESCALATION_TEMPLATE",
    "PRIVATE_TYPES",
    "WAKING_FIELDS",
    "TelegramMention",
    "addressed",
    "ask_body",
    "escalation_summons",
    "read_mention",
    "speech",
    "to_summons",
    "without_handle",
]
