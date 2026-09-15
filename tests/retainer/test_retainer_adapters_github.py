"""GitHub: a mention becomes a summons, an answer becomes a comment.

The test that matters most is ``test_a_retainer_does_not_answer_its_own_comment``
and its companion ``test_the_quoted_copy_of_our_own_ask_is_not_a_new_mention``.
Together they close the loop that cost the product this design was drawn against
a weekly budget in twenty-one seconds: an agent reacting to the echo of its own
voice. They are written as a genuine round trip — the body the adapter itself
produced is fed back in as a delivery — rather than as a hand-written fixture
that could drift away from what the adapter really posts.
"""

from __future__ import annotations

from typing import Any

import pytest

from ronin.retainer.adapters.common import APPROVING, read_answer, strip_quotes_and_code
from ronin.retainer.adapters.github import (
    WAKING_EVENTS,
    Mention,
    ask_body,
    escalation_summons,
    mentions,
    read_mention,
    to_summons,
    without_handle,
)
from ronin.retainer.model import Channel, SummonsKind

HANDLE = "ronin"
US = "ronin[bot]"
THEM = "rohithkandula19"


def comment(**kwargs: Any) -> dict[str, Any]:
    body = kwargs.pop("body", "@ronin please look at this")
    author = kwargs.pop("author", THEM)
    return {
        "action": kwargs.pop("action", "created"),
        "repository": {"full_name": kwargs.pop("repo", "rohithkandula19/Ronin")},
        "issue": {"number": kwargs.pop("number", 258)},
        "comment": {"id": kwargs.pop("id", 4242), "body": body, "user": {"login": author}},
        **kwargs,
    }


def opened_issue(**kwargs: Any) -> dict[str, Any]:
    return {
        "action": "opened",
        "repository": {"full_name": "rohithkandula19/Ronin"},
        "issue": {
            "number": kwargs.pop("number", 300),
            "title": kwargs.pop("title", "CI is red"),
            "body": kwargs.pop("body", "@ronin can you look?"),
            "user": {"login": kwargs.pop("author", THEM)},
        },
    }


def read(body: dict[str, Any], *, event: str = "issue_comment", ourselves: str = US) -> Any:
    return read_mention(body, event=event, handle=HANDLE, ourselves=ourselves)


# --------------------------------------------------------------------------- #
# Loop prevention — the expensive failure
# --------------------------------------------------------------------------- #


def test_a_retainer_does_not_answer_its_own_comment() -> None:
    assert read(comment(author=US)) is None


def test_the_check_is_case_insensitive_because_logins_are() -> None:
    assert read(comment(author="Ronin[Bot]")) is None


def test_the_quoted_copy_of_our_own_ask_is_not_a_new_mention() -> None:
    """A real round trip: what the adapter posts, quoted back by the reply UI."""
    posted = ask_body(
        retainer="Sentry",
        handle=HANDLE,
        tool="bash",
        rendered="git push --force origin main",
        escalation="esc-abc123",
    )
    quoted = "\n".join(f"> {line}" for line in posted.splitlines())
    assert read(comment(body=quoted, author=THEM)) is None


def test_our_own_ask_posted_by_us_is_dropped_even_unquoted() -> None:
    posted = ask_body(
        retainer="Sentry", handle=HANDLE, tool="bash", rendered="rm -rf build", escalation="esc-1"
    )
    assert read(comment(body=posted, author=US)) is None


def test_with_no_identity_configured_no_bot_may_wake_a_retainer() -> None:
    """We cannot tell our own comments from another App's, so silence beats a loop."""
    assert read(comment(author="dependabot[bot]"), ourselves="") is None
    assert read(comment(author=THEM), ourselves="") is not None


def test_a_different_bot_may_still_be_ignored_deliberately() -> None:
    """With an identity set, another App is a normal author — the operator decides."""
    assert read(comment(author="dependabot[bot]")) is not None


# --------------------------------------------------------------------------- #
# Where a mention counts
# --------------------------------------------------------------------------- #


def test_a_plain_mention_counts() -> None:
    found = read(comment(body="@ronin have a look"))
    assert found is not None
    assert found.text == "have a look"


def test_a_mention_inside_a_fenced_block_is_documentation() -> None:
    assert read(comment(body="try this:\n```\n@ronin do the thing\n```\n")) is None


def test_a_mention_inside_inline_code_does_not_count() -> None:
    assert read(comment(body="the handle is `@ronin` by default")) is None


def test_a_mention_inside_a_quote_is_somebody_repeating_themselves() -> None:
    assert read(comment(body="> @ronin do the thing\n\nI agree")) is None


def test_a_quote_marker_inside_a_fence_is_code_not_a_quote() -> None:
    """Fences are removed first, so an unbalanced fence cannot swallow the message."""
    text = "```\n> not a quote\n```\n@ronin please look"
    assert read(comment(body=text)) is not None


def test_a_longer_handle_is_not_a_match() -> None:
    assert read(comment(body="@ronindev should see this")) is None


def test_an_email_shaped_string_is_not_a_mention() -> None:
    assert read(comment(body="write to me@ronin.example")) is None


def test_a_mention_is_case_insensitive() -> None:
    assert read(comment(body="@Ronin please look")) is not None


def test_a_comment_with_no_mention_is_ignored() -> None:
    assert read(comment(body="looks good to me")) is None


def test_an_empty_handle_matches_nothing() -> None:
    assert not mentions("@ronin anything", "")


def test_stripping_leaves_the_prose_alone() -> None:
    assert strip_quotes_and_code("plain words").strip() == "plain words"


def test_without_handle_removes_only_the_address() -> None:
    assert without_handle("@ronin fix @ronin the build", HANDLE) == "fix  the build"


# --------------------------------------------------------------------------- #
# Which events wake a Retainer
# --------------------------------------------------------------------------- #


def test_an_opened_issue_carries_its_title_and_body() -> None:
    found = read_mention(opened_issue(), event="issues", handle=HANDLE, ourselves=US)
    assert found is not None
    assert found.text.startswith("CI is red")
    assert "can you look?" in found.text


def test_an_edited_comment_still_counts() -> None:
    assert read(comment(action="edited")) is not None


def test_an_action_that_is_not_listed_is_ignored() -> None:
    assert read(comment(action="deleted")) is None


def test_an_event_that_is_not_listed_is_ignored() -> None:
    assert read(comment(), event="push") is None
    assert read(comment(), event="star") is None


def test_the_waking_set_is_deliberately_small() -> None:
    assert set(WAKING_EVENTS) == {
        "issue_comment",
        "pull_request_review_comment",
        "issues",
        "pull_request",
    }


# --------------------------------------------------------------------------- #
# Malformed deliveries
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "broken",
    [
        {"action": "created"},
        {"action": "created", "repository": {}, "issue": {"number": 1}},
        {"action": "created", "repository": {"full_name": "o/n"}, "issue": {}},
    ],
)
def test_a_delivery_missing_what_it_needs_is_ignored_rather_than_crashing(
    broken: dict[str, Any],
) -> None:
    assert read_mention(broken, event="issue_comment", handle=HANDLE, ourselves=US) is None


def test_an_author_only_in_sender_is_still_found() -> None:
    body = comment()
    body["comment"].pop("user")
    body["sender"] = {"login": THEM}
    assert read(body) is not None


def test_a_delivery_with_no_author_anywhere_is_ignored() -> None:
    body = comment()
    body["comment"].pop("user")
    assert read(body) is None


def test_a_number_of_zero_or_missing_is_ignored() -> None:
    assert read(comment(number=0)) is None


# --------------------------------------------------------------------------- #
# Normalising
# --------------------------------------------------------------------------- #


def test_the_thread_key_is_stable_across_events() -> None:
    from_comment = read(comment())
    from_issue = read_mention(opened_issue(number=258), event="issues", handle=HANDLE, ourselves=US)
    assert from_comment is not None and from_issue is not None
    assert from_comment.thread == from_issue.thread == "rohithkandula19/Ronin#258"


def test_a_summons_forgets_it_came_from_github_except_for_the_channel() -> None:
    found = read(comment())
    assert found is not None
    summons = to_summons(found, "sentry")
    assert summons.kind is SummonsKind.MENTION
    assert summons.channel is Channel.GITHUB
    assert summons.thread == "rohithkandula19/Ronin#258"
    assert summons.actor == THEM
    assert summons.escalation == ""


def test_an_escalation_reply_is_a_different_kind_of_summons() -> None:
    found = read(comment(body="@ronin allow esc-abc123"))
    assert found is not None
    summons = escalation_summons(found, "sentry", "esc-abc123")
    assert summons.kind is SummonsKind.REPLY
    assert summons.escalation == "esc-abc123"


def test_a_comment_node_id_is_recorded_for_the_idempotency_key() -> None:
    found = read(comment(id=99))
    assert found is not None and found.node == "comment-99"
    opened = read_mention(opened_issue(), event="issues", handle=HANDLE, ourselves=US)
    assert opened is not None and opened.node == "issues-300"


def test_mention_carries_the_login_not_a_display_name() -> None:
    """Authority checks use this, so it must be the handle GitHub verified."""
    found = read(comment(author=THEM))
    assert found is not None and found.actor == THEM


# --------------------------------------------------------------------------- #
# Answering an escalation
# --------------------------------------------------------------------------- #


def test_allow_and_approve_both_mean_yes() -> None:
    assert read_answer("@ronin allow esc-abc123") == ("esc-abc123", True)
    assert read_answer("@ronin approve esc-abc123") == ("esc-abc123", True)
    assert {"allow", "approve"} == APPROVING


def test_deny_and_reject_both_mean_no() -> None:
    assert read_answer("deny esc-abc123") == ("esc-abc123", False)
    assert read_answer("reject esc-abc123") == ("esc-abc123", False)


def test_an_answer_is_case_insensitive() -> None:
    assert read_answer("ALLOW esc-abc123") == ("esc-abc123", True)


def test_a_comment_that_is_not_an_answer_reads_as_none() -> None:
    assert read_answer("looks fine to me") is None
    assert read_answer("allow it") is None
    assert read_answer("esc-abc123") is None


def test_our_own_instructions_quoted_back_are_not_an_answer() -> None:
    """The template tells people what to type. Quoting it must not decide anything."""
    posted = ask_body(
        retainer="Sentry", handle=HANDLE, tool="bash", rendered="rm -rf /", escalation="esc-abc123"
    )
    quoted = "\n".join(f"> {line}" for line in posted.splitlines())
    assert read_answer(quoted) is None


def test_a_fresh_ask_is_not_readable_as_an_answer_to_itself() -> None:
    """The instructions live in inline code, so the parser never sees them.

    A second layer, independent of the author check: even a human who copies the
    ask verbatim into a new comment has not answered anything, because the words
    that would decide it are formatted as code.
    """
    posted = ask_body(
        retainer="Sentry", handle=HANDLE, tool="bash", rendered="rm -rf /", escalation="esc-abc123"
    )
    assert read_answer(posted) is None


# --------------------------------------------------------------------------- #
# What gets posted
# --------------------------------------------------------------------------- #


def test_the_ask_says_what_will_run_and_how_to_answer() -> None:
    text = ask_body(
        retainer="Sentry",
        handle=HANDLE,
        tool="bash",
        rendered="git push --force origin main",
        escalation="esc-abc123",
    )
    assert "Sentry needs an approval" in text
    assert "git push --force origin main" in text
    assert "@ronin allow esc-abc123" in text
    assert "@ronin deny esc-abc123" in text


def test_the_ask_states_the_limit_of_an_approval() -> None:
    text = ask_body(retainer="Sentry", handle=HANDLE, tool="bash", rendered="x", escalation="esc-1")
    assert "does not undo anything already done" in text
    assert "Anyone with write access" in text


def test_the_ask_names_our_handle_but_not_in_a_way_that_reads_as_a_mention() -> None:
    """Two independent reasons our own ask cannot wake us, and both are tested.

    The handle appears only inside inline code, so the mention detector does not
    see it; and the author check drops our own comments before anything looks.
    Either alone would do; having both means one regressing is not an outage.
    """
    text = ask_body(retainer="Sentry", handle=HANDLE, tool="bash", rendered="x", escalation="esc-1")
    assert "@ronin" in text
    assert not mentions(text, HANDLE)
    assert read(comment(body=text, author=US)) is None
    assert read(comment(body=text, author=THEM)) is None


def test_a_mention_dataclass_is_frozen() -> None:
    found = Mention(repo="o/n", number=1, actor="a", text="t", event="issues", action="opened")
    with pytest.raises(AttributeError):
        found.text = "changed"  # type: ignore[misc]


def test_an_opened_pull_request_wakes_a_retainer_too() -> None:
    body = {
        "action": "opened",
        "repository": {"full_name": "rohithkandula19/Ronin"},
        "number": 259,
        "pull_request": {
            "number": 259,
            "title": "fix the thing",
            "body": "@ronin review please",
            "user": {"login": THEM},
        },
    }
    found = read_mention(body, event="pull_request", handle=HANDLE, ourselves=US)
    assert found is not None
    assert found.thread == "rohithkandula19/Ronin#259"
    assert found.text.startswith("fix the thing")


def test_a_pull_request_number_only_at_the_top_level_is_still_found() -> None:
    """Some deliveries carry the number beside the subject rather than inside it."""
    body = {
        "action": "opened",
        "repository": {"full_name": "rohithkandula19/Ronin"},
        "number": 260,
        "pull_request": {"title": "t", "body": "@ronin look", "user": {"login": THEM}},
    }
    found = read_mention(body, event="pull_request", handle=HANDLE, ourselves=US)
    assert found is not None and found.number == 260


def test_an_opened_subject_that_is_not_an_object_is_ignored() -> None:
    body = {
        "action": "opened",
        "repository": {"full_name": "rohithkandula19/Ronin"},
        "number": 261,
        "pull_request": None,
        "sender": {"login": THEM},
    }
    assert read_mention(body, event="pull_request", handle=HANDLE, ourselves=US) is None


def test_an_issue_with_no_body_is_just_its_title() -> None:
    body = opened_issue(body=None, title="@ronin CI is red")
    found = read_mention(body, event="issues", handle=HANDLE, ourselves=US)
    assert found is not None and found.text == "CI is red"
