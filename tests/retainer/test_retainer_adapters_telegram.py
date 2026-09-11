"""Telegram: a message becomes a summons, an answer becomes a reply.

The third adapter's job in this suite is to falsify the claim that the core is
platform-agnostic. Telegram addresses bots by suffixed handle, needs no address
at all in a private chat, reports its own code spans as offsets, and keys a
conversation on a chat or a topic inside one. If any of that had needed a new
field on the shared types, this file is where it would show.
"""

from __future__ import annotations

from typing import Any

import pytest

from ronin.retainer.adapters.common import read_answer
from ronin.retainer.adapters.telegram import (
    CODE_ENTITIES,
    PRIVATE_TYPES,
    WAKING_FIELDS,
    TelegramMention,
    addressed,
    ask_body,
    escalation_summons,
    read_mention,
    speech,
    to_summons,
    without_handle,
)
from ronin.retainer.model import Channel, SummonsKind

HANDLE = "ronin_bot"
THEM = "8675309"
CHAT = "-1001234567890"


def update(**kwargs: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": kwargs.pop("message_id", 42),
        "from": {
            "id": int(kwargs.pop("user", THEM)),
            "is_bot": kwargs.pop("is_bot", False),
        },
        "chat": {"id": int(CHAT), "type": kwargs.pop("chat_type", "supergroup")},
        "text": kwargs.pop("text", "@ronin_bot please look at this"),
    }
    for key in ("entities", "message_thread_id", "caption", "caption_entities"):
        if key in kwargs:
            message[key] = kwargs.pop(key)
    if kwargs.pop("no_text", False):
        message.pop("text")
    return {kwargs.pop("field", "message"): message, **kwargs}


def read(body: dict[str, Any], **kwargs: Any) -> TelegramMention | None:
    return read_mention(body, handle=kwargs.pop("handle", HANDLE), **kwargs)


# --------------------------------------------------------------------------- #
# A mention is a name again, with a suffix
# --------------------------------------------------------------------------- #


def test_a_suffixed_handle_counts() -> None:
    found = read(update())
    assert found is not None
    assert found.text == "please look at this"


def test_a_command_addressed_to_the_bot_counts() -> None:
    found = read(update(text="/ask@ronin_bot what broke?"))
    assert found is not None
    assert found.text == "/ask what broke?"


def test_a_command_addressed_to_another_bot_does_not() -> None:
    assert read(update(text="/ask@someone_else what broke?")) is None


def test_a_longer_handle_is_not_a_match() -> None:
    assert read(update(text="@ronin_bot_v2 hello")) is None


def test_the_bare_handle_without_the_suffix_is_not_a_match() -> None:
    assert read(update(text="@ronin hello")) is None


def test_an_empty_handle_matches_nothing_in_a_group() -> None:
    assert not addressed("@ronin_bot hi", None, "", private=False)
    assert without_handle("  hi  ", "") == "hi"


# --------------------------------------------------------------------------- #
# A private chat needs no address
# --------------------------------------------------------------------------- #


def test_every_message_in_a_private_chat_is_addressed_to_the_bot() -> None:
    found = read(update(chat_type="private", text="what broke?"))
    assert found is not None
    assert found.private
    assert found.text == "what broke?"


def test_the_same_message_in_a_group_is_not() -> None:
    assert read(update(text="what broke?")) is None


def test_private_is_one_flag_rather_than_two_code_paths() -> None:
    assert addressed("anything at all", None, "", private=True)
    assert {"private"} == PRIVATE_TYPES


# --------------------------------------------------------------------------- #
# Entities are authoritative where Telegram sent them
# --------------------------------------------------------------------------- #


def test_a_mention_inside_a_reported_code_span_is_documentation() -> None:
    text = "the handle is @ronin_bot by default"
    entities = [{"type": "code", "offset": 14, "length": 10}]
    assert read(update(text=text, entities=entities)) is None


def test_a_mention_outside_a_reported_code_span_still_counts() -> None:
    text = "@ronin_bot see `@ronin_bot` in the docs"
    entities = [{"type": "code", "offset": 15, "length": 12}]
    assert read(update(text=text, entities=entities)) is not None


def test_telegrams_offsets_win_over_reparsing() -> None:
    """No backticks in the text at all — only Telegram knows it is code."""
    text = "run @ronin_bot now"
    assert speech(text, None).strip() == "run @ronin_bot now"
    entities = [{"type": "pre", "offset": 0, "length": len(text)}]
    assert speech(text, entities).strip() == ""


def test_the_shared_stripping_is_the_fallback_when_no_entities_arrive() -> None:
    assert read(update(text="`@ronin_bot` is the handle")) is None
    assert read(update(text="> @ronin_bot said this earlier")) is None


def test_entities_that_are_not_code_are_left_alone() -> None:
    entities = [{"type": "bold", "offset": 0, "length": 10}]
    assert read(update(entities=entities)) is not None


@pytest.mark.parametrize(
    "junk",
    [
        "not a list",
        [{"type": "code"}],
        [{"type": "code", "offset": "x", "length": 3}],
        [{"type": "code", "offset": 0, "length": 0}],
        ["not a mapping"],
    ],
)
def test_malformed_entities_fall_back_rather_than_crashing(junk: Any) -> None:
    assert read(update(entities=junk)) is not None


def test_an_out_of_range_span_does_not_reach_past_the_text() -> None:
    entities = [{"type": "code", "offset": -5, "length": 500}]
    assert speech("@ronin_bot hello", entities).strip() == ""


def test_the_code_entity_set_is_what_telegram_calls_code() -> None:
    assert {"code", "pre"} == CODE_ENTITIES


# --------------------------------------------------------------------------- #
# A thread is a chat, or a topic inside one
# --------------------------------------------------------------------------- #


def test_a_plain_group_keys_on_the_chat() -> None:
    found = read(update())
    assert found is not None and found.thread == CHAT


def test_a_forum_topic_keys_on_both() -> None:
    found = read(update(message_thread_id=77))
    assert found is not None and found.thread == f"{CHAT}/77"


def test_two_topics_in_one_chat_are_two_conversations() -> None:
    one = read(update(message_thread_id=1))
    two = read(update(message_thread_id=2))
    assert one is not None and two is not None
    assert one.thread != two.thread


# --------------------------------------------------------------------------- #
# Loop prevention
# --------------------------------------------------------------------------- #


def test_no_bot_may_wake_a_retainer() -> None:
    """Telegram groups routinely hold several; answering one is a loop with it."""
    assert read(update(is_bot=True)) is None


def test_our_own_id_is_dropped_even_if_telegram_forgot_the_flag() -> None:
    assert read(update(user="99001"), ourselves="99001") is None


def test_our_own_ask_cannot_wake_us_because_its_instructions_are_code() -> None:
    posted = ask_body(retainer="Sentry", tool="bash", rendered="rm -rf build", escalation="esc-1")
    assert read(update(text=posted)) is None


# --------------------------------------------------------------------------- #
# Which updates count, and malformed ones
# --------------------------------------------------------------------------- #


def test_an_edited_message_still_counts() -> None:
    assert read(update(field="edited_message")) is not None
    assert WAKING_FIELDS == ("message", "edited_message")


def test_an_update_that_is_not_a_message_is_ignored() -> None:
    assert read({"callback_query": {"id": "1"}}) is None
    assert read({}) is None


def test_a_caption_on_a_photo_counts_as_the_message() -> None:
    body = update(no_text=True, caption="@ronin_bot what is this?")
    found = read(body)
    assert found is not None and found.text == "what is this?"


@pytest.mark.parametrize("broken", ["from", "chat"])
def test_a_message_missing_who_or_where_is_ignored(broken: str) -> None:
    body = update()
    body["message"].pop(broken)
    assert read(body) is None


def test_a_sender_with_no_id_is_ignored() -> None:
    body = update()
    body["message"]["from"] = {"is_bot": False}
    assert read(body) is None


def test_a_chat_with_no_id_is_ignored() -> None:
    body = update()
    body["message"]["chat"] = {"type": "supergroup"}
    assert read(body) is None


# --------------------------------------------------------------------------- #
# Normalising against the shared types — the point of the exercise
# --------------------------------------------------------------------------- #


def test_a_summons_forgets_it_came_from_telegram_except_for_the_channel() -> None:
    found = read(update())
    assert found is not None
    summons = to_summons(found, "sentry")
    assert summons.kind is SummonsKind.MENTION
    assert summons.channel is Channel.TELEGRAM
    assert summons.thread == CHAT
    assert summons.actor == THEM


def test_the_actor_is_the_numeric_id_not_a_username() -> None:
    """Usernames change; ids do not, and authority checks consume this."""
    found = read(update())
    assert found is not None and found.actor.isdigit()


def test_an_escalation_reply_is_a_different_kind_of_summons() -> None:
    found = read(update(text="@ronin_bot allow esc-abc123"))
    assert found is not None
    summons = escalation_summons(found, "sentry", "esc-abc123")
    assert summons.kind is SummonsKind.REPLY
    assert summons.escalation == "esc-abc123"


def test_the_shared_answer_parser_needed_no_telegram_specific_change() -> None:
    found = read(update(text="@ronin_bot allow esc-abc123"))
    assert found is not None
    assert read_answer(found.text) == ("esc-abc123", True)


def test_all_three_adapters_produce_the_same_shape() -> None:
    """The claim, as an assertion: three platforms, one Summons, no extra fields."""
    from dataclasses import fields

    from ronin.retainer.adapters import github, slack

    telegram_summons = to_summons(read(update()), "sentry")  # type: ignore[arg-type]
    github_mention = github.Mention(
        repo="o/n", number=1, actor="a", text="t", event="issues", action="opened"
    )
    slack_mention = slack.SlackMention(
        team="T", channel="C", thread_ts="1", ts="1", actor="U", text="t"
    )
    shapes = {
        telegram_summons,
        github.to_summons(github_mention, "sentry"),
        slack.to_summons(slack_mention, "sentry"),
    }
    assert len({tuple(f.name for f in fields(s)) for s in shapes}) == 1
    assert {s.channel for s in shapes} == {Channel.TELEGRAM, Channel.GITHUB, Channel.SLACK}


# --------------------------------------------------------------------------- #
# What gets posted
# --------------------------------------------------------------------------- #


def test_the_ask_avoids_markdownv2_because_one_missed_escape_rejects_it() -> None:
    text = ask_body(retainer="Sentry", tool="bash", rendered="a-b.c!", escalation="esc-1")
    assert "\\" not in text
    assert "a-b.c!" in text


def test_the_ask_says_what_will_run_and_how_to_answer() -> None:
    text = ask_body(
        retainer="Sentry", tool="bash", rendered="git push --force", escalation="esc-abc123"
    )
    assert "git push --force" in text
    assert "allow esc-abc123" in text
    assert "does not undo anything already done" in text


def test_a_telegram_mention_dataclass_is_frozen() -> None:
    found = TelegramMention(chat="1", topic="", message_id="1", actor="2", text="t")
    with pytest.raises(AttributeError):
        found.text = "changed"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# The address rule, which differs from GitHub's on purpose
# --------------------------------------------------------------------------- #


def test_an_email_shaped_string_is_still_not_an_address() -> None:
    """The looser lookbehind must not make every email a mention."""
    assert read(update(text="write to me@ronin_bot.example")) is None
    assert read(update(text="see ronin_bot@example.com")) is None


def test_a_double_at_is_not_an_address() -> None:
    assert read(update(text="@@ronin_bot")) is None


def test_a_reported_bot_command_entity_is_authoritative() -> None:
    text = "/ask@ronin_bot what broke?"
    entities = [{"type": "bot_command", "offset": 0, "length": 15}]
    assert read(update(text=text, entities=entities)) is not None


def test_a_reported_mention_entity_is_authoritative() -> None:
    text = "hey @ronin_bot look"
    entities = [{"type": "mention", "offset": 4, "length": 10}]
    assert read(update(text=text, entities=entities)) is not None


def test_a_reported_mention_of_somebody_else_is_not_us() -> None:
    text = "hey @someone_else look"
    entities = [{"type": "mention", "offset": 4, "length": 13}]
    assert read(update(text=text, entities=entities)) is None


def test_an_address_entity_inside_a_code_span_does_not_count() -> None:
    """Both entity kinds present, and the code span wins."""
    text = "`/ask@ronin_bot`"
    entities = [
        {"type": "code", "offset": 0, "length": 16},
        {"type": "bot_command", "offset": 1, "length": 14},
    ]
    assert read(update(text=text, entities=entities)) is None


def test_the_address_entity_set_names_both_shapes() -> None:
    from ronin.retainer.adapters.telegram import ADDRESS_ENTITIES

    assert {"mention", "bot_command"} == ADDRESS_ENTITIES


def test_github_keeps_its_stricter_rule_because_it_has_no_command_form() -> None:
    """The two adapters differ here deliberately; neither is a copy of the other."""
    from ronin.retainer.adapters.github import mentions

    assert not mentions("/ask@ronin what broke?", "ronin")
    assert addressed("/ask@ronin_bot what broke?", None, HANDLE, private=False)
