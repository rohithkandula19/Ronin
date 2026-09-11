"""Slack: an app mention becomes a summons, an answer becomes a threaded reply.

Slack is where "the core is platform-agnostic" is either true or exposed. Its
mention is a user id rather than a name, its thread key has two parts, and two
of its deliveries are not summonses at all. The tests below check each of those
against the *shared* types, so a bend in one of them would show up here rather
than three adapters later.
"""

from __future__ import annotations

from typing import Any

import pytest

from ronin.retainer.adapters.slack import (
    IGNORED_SUBTYPES,
    RETRY_HEADER,
    WAKING_TYPES,
    SlackMention,
    addressed,
    ask_body,
    challenge_of,
    escalation_summons,
    is_retry,
    read_mention,
    to_summons,
    without_link,
)
from ronin.retainer.model import Channel, SummonsKind

BOT = "U07RONIN"
THEM = "U01HUMAN"
CHANNEL = "C0ENG"
TS = "1757155200.001900"


def delivery(**kwargs: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": kwargs.pop("type", "app_mention"),
        "user": kwargs.pop("user", THEM),
        "channel": kwargs.pop("channel", CHANNEL),
        "ts": kwargs.pop("ts", TS),
        "text": kwargs.pop("text", f"<@{BOT}> please look at this"),
    }
    for key in ("subtype", "bot_id", "thread_ts"):
        if key in kwargs:
            event[key] = kwargs.pop(key)
    return {
        "type": kwargs.pop("envelope", "event_callback"),
        "team_id": kwargs.pop("team", "T0RONIN"),
        "event": event,
        **kwargs,
    }


def read(body: dict[str, Any], **kwargs: Any) -> SlackMention | None:
    return read_mention(body, bot_user=kwargs.pop("bot_user", BOT), **kwargs)


# --------------------------------------------------------------------------- #
# A mention is an id, not a name
# --------------------------------------------------------------------------- #


def test_a_linked_mention_of_the_bot_counts() -> None:
    found = read(delivery())
    assert found is not None
    assert found.text == "please look at this"


def test_a_mention_of_somebody_else_does_not_count() -> None:
    assert read(delivery(text=f"<@{THEM}> can you look?")) is None


def test_a_bare_handle_is_not_a_mention_because_slack_never_sends_one() -> None:
    assert read(delivery(text="@ronin please look")) is None


def test_a_link_with_a_display_name_still_matches_the_id() -> None:
    found = read(delivery(text=f"<@{BOT}|ronin> please look"))
    assert found is not None


def test_a_mention_in_a_fenced_block_is_documentation() -> None:
    assert read(delivery(text=f"```\n<@{BOT}> do the thing\n```")) is None


def test_a_mention_in_a_quote_is_somebody_repeating_it() -> None:
    assert read(delivery(text=f"> <@{BOT}> do the thing\n\nagreed")) is None


def test_an_empty_bot_id_matches_nothing() -> None:
    assert not addressed(f"<@{BOT}> hello", "")
    assert without_link("  hello  ", "") == "hello"


def test_the_id_must_match_exactly_not_by_prefix() -> None:
    assert not addressed(f"<@{BOT}EXTRA> hello", BOT)


# --------------------------------------------------------------------------- #
# A thread key has two parts
# --------------------------------------------------------------------------- #


def test_a_message_outside_a_thread_is_its_own_root() -> None:
    found = read(delivery())
    assert found is not None
    assert found.thread == f"{CHANNEL}/{TS}"
    assert found.thread_ts == TS


def test_a_reply_inside_a_thread_keys_on_the_parent() -> None:
    found = read(delivery(ts="1757155300.002000", thread_ts=TS))
    assert found is not None
    assert found.thread == f"{CHANNEL}/{TS}"
    assert found.ts == "1757155300.002000"


def test_the_same_timestamp_in_two_channels_is_two_conversations() -> None:
    here = read(delivery(channel="C0ENG"))
    there = read(delivery(channel="C0OPS"))
    assert here is not None and there is not None
    assert here.thread != there.thread


def test_a_reply_in_a_known_thread_needs_no_fresh_mention() -> None:
    """How people actually talk: they stop re-addressing you after the first message."""
    known = frozenset({f"{CHANNEL}/{TS}"})
    plain = delivery(type="message", text="and also the tests", thread_ts=TS)
    assert read(plain) is None
    assert read(plain, known_threads=known) is not None


def test_a_plain_message_in_an_unknown_thread_is_still_ignored() -> None:
    known = frozenset({f"{CHANNEL}/somewhere-else"})
    plain = delivery(type="message", text="chatting", thread_ts=TS)
    assert read(plain, known_threads=known) is None


# --------------------------------------------------------------------------- #
# Not every delivery is a summons
# --------------------------------------------------------------------------- #


def test_the_url_verification_handshake_is_answered_not_summoned() -> None:
    body = {"type": "url_verification", "challenge": "3eZbrw1a"}
    assert challenge_of(body) == "3eZbrw1a"
    assert read_mention(body, bot_user=BOT) is None


def test_an_ordinary_delivery_is_not_a_challenge() -> None:
    assert challenge_of(delivery()) == ""


def test_a_challenge_that_is_not_a_string_is_not_a_challenge() -> None:
    assert challenge_of({"type": "url_verification", "challenge": 17}) == ""


def test_a_redelivery_is_recognisable_from_its_header() -> None:
    assert is_retry({RETRY_HEADER: "1"})
    assert is_retry({"x-slack-retry-num": "2"})
    assert not is_retry({})
    assert not is_retry({RETRY_HEADER: ""})


def test_an_envelope_that_is_not_an_event_callback_is_ignored() -> None:
    assert read(delivery(envelope="something_else")) is None


def test_an_event_that_is_not_an_object_is_ignored() -> None:
    assert read_mention({"type": "event_callback", "event": "oops"}, bot_user=BOT) is None


def test_a_delivery_with_no_event_at_all_is_ignored() -> None:
    assert read_mention({"type": "event_callback"}, bot_user=BOT) is None


# --------------------------------------------------------------------------- #
# Loop prevention
# --------------------------------------------------------------------------- #


def test_the_bot_does_not_answer_itself_by_user_id() -> None:
    assert read(delivery(user=BOT)) is None


def test_a_bot_id_on_the_event_is_enough_to_drop_it() -> None:
    """Set even where the subtype does not say bot_message."""
    assert read(delivery(bot_id="B0RONIN")) is None


@pytest.mark.parametrize("subtype", sorted(IGNORED_SUBTYPES))
def test_an_ignored_subtype_is_not_somebody_talking(subtype: str) -> None:
    assert read(delivery(type="message", subtype=subtype)) is None


def test_our_own_ask_cannot_wake_us_even_ignoring_the_author_check() -> None:
    """The instructions are in backticks, so `addressed` never sees the id."""
    posted = ask_body(retainer="Sentry", tool="bash", rendered="rm -rf build", escalation="esc-1")
    assert read(delivery(text=posted, user=THEM)) is None


def test_the_waking_and_ignored_sets_are_deliberately_small() -> None:
    assert {"app_mention", "message"} == WAKING_TYPES
    assert "bot_message" in IGNORED_SUBTYPES


# --------------------------------------------------------------------------- #
# Malformed deliveries
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("missing", ["user", "channel", "ts"])
def test_a_delivery_missing_what_it_needs_is_ignored(missing: str) -> None:
    body = delivery()
    body["event"][missing] = ""
    assert read(body) is None


def test_an_unlisted_event_type_is_ignored() -> None:
    assert read(delivery(type="reaction_added")) is None


# --------------------------------------------------------------------------- #
# Normalising against the shared types
# --------------------------------------------------------------------------- #


def test_a_summons_forgets_it_came_from_slack_except_for_the_channel() -> None:
    found = read(delivery())
    assert found is not None
    summons = to_summons(found, "sentry")
    assert summons.kind is SummonsKind.MENTION
    assert summons.channel is Channel.SLACK
    assert summons.thread == f"{CHANNEL}/{TS}"
    assert summons.actor == THEM


def test_the_summons_carries_the_user_id_because_authority_consumes_it() -> None:
    found = read(delivery())
    assert found is not None
    assert to_summons(found, "sentry").actor == THEM


def test_an_escalation_reply_is_a_different_kind_of_summons() -> None:
    found = read(delivery(text=f"<@{BOT}> allow esc-abc123"))
    assert found is not None
    summons = escalation_summons(found, "sentry", "esc-abc123")
    assert summons.kind is SummonsKind.REPLY
    assert summons.escalation == "esc-abc123"


def test_the_shared_answer_parser_works_on_slack_text_unchanged() -> None:
    """Nothing about reading an answer is platform-specific, so nothing is duplicated."""
    from ronin.retainer.adapters.common import read_answer

    found = read(delivery(text=f"<@{BOT}> allow esc-abc123"))
    assert found is not None
    assert read_answer(found.text) == ("esc-abc123", True)


def test_the_team_is_recorded_for_a_workspace_scoped_lookup() -> None:
    found = read(delivery(team="T0OTHER"))
    assert found is not None and found.team == "T0OTHER"


# --------------------------------------------------------------------------- #
# What gets posted
# --------------------------------------------------------------------------- #


def test_the_ask_uses_slack_mrkdwn_not_markdown() -> None:
    text = ask_body(retainer="Sentry", tool="bash", rendered="x", escalation="esc-1")
    assert text.startswith("*Sentry needs an approval to continue.*")
    assert "**" not in text


def test_the_ask_says_what_will_run_and_how_to_answer() -> None:
    text = ask_body(
        retainer="Sentry", tool="bash", rendered="git push --force", escalation="esc-abc123"
    )
    assert "git push --force" in text
    assert "allow esc-abc123" in text
    assert "deny esc-abc123" in text
    assert "does not undo anything already done" in text


def test_a_slack_mention_dataclass_is_frozen() -> None:
    found = SlackMention(team="T", channel="C", thread_ts="1", ts="1", actor="U", text="t")
    with pytest.raises(AttributeError):
        found.text = "changed"  # type: ignore[misc]
