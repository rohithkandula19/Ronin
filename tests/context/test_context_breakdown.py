"""Where the window's tokens are, by category.

The cost ledger already answers "what did this cost and which model spent it". It
cannot answer "what is *in* the window", because it counts requests and dollars, not
occupancy. On a 32k local model those are different questions with different remedies:
a ledger row says switch model, a breakdown row says shrink the repo map or let
compaction run.

The load-bearing test here is the accounting one. Every character
:func:`~ronin.context.compaction.render_message` produces has to land in exactly one
category, because a breakdown whose parts do not sum to its whole is worse than no
breakdown -- it invites the reader to trust a percentage that is quietly short. That is
also why the attribution lives beside ``render_message``: add a block type there and
forget it here, and this file fails rather than a category silently under-reporting.
"""

from __future__ import annotations

import pytest

from ronin.context.compaction import (
    BREAKDOWN_CATEGORIES,
    ContextBreakdown,
    context_breakdown,
    render_message,
    transcript_tokens,
)
from ronin.core.types import Message, Role, Text, ToolResultBlock, ToolUse


def session() -> list[Message]:
    """A transcript with one of every block type, in the shapes they really occur."""
    return [
        Message(role=Role.USER, content_blocks=(Text(text="add a retry to fetch_user"),)),
        Message(
            role=Role.ASSISTANT,
            content_blocks=(
                Text(text="Reading the file first."),
                ToolUse(id="c1", name="read", arguments={"path": "src/net.py", "offset": 40}),
            ),
        ),
        Message(
            role=Role.TOOL,
            content_blocks=(
                ToolResultBlock(tool_use_id="c1", content="1\tdef fetch_user():\n" * 40),
            ),
        ),
        Message(
            role=Role.TOOL,
            content_blocks=(ToolResultBlock(tool_use_id="c2", content="boom", is_error=True),),
        ),
        Message(role=Role.SYSTEM, content_blocks=(Text(text="[compacted] earlier turns"),)),
    ]


# --------------------------------------------------------------------------- #
# the accounting invariant
# --------------------------------------------------------------------------- #


def test_every_rendered_character_lands_in_exactly_one_category() -> None:
    """The whole basis for trusting a percentage.

    Not "roughly adds up" -- exactly. Characters are accumulated per category and
    converted to tokens once, so the parts sum to the total by construction rather
    than by luck.
    """
    messages = session()
    rendered = sum(len(render_message(message)) for message in messages)

    breakdown = context_breakdown(messages)

    assert breakdown.total_chars == rendered


def test_the_pinned_prefix_is_counted_even_though_it_is_not_in_the_transcript() -> None:
    """A breakdown that omitted the repo map would call a full window mostly empty.

    RONIN.md and the repo map live in the provider's stable prefix, not in
    ``messages``, which is the same reason ``should_compact`` takes
    ``pinned_prefix_tokens``. They are the two costs a user can actually configure
    away, so leaving them out would hide the most actionable rows.
    """
    messages = session()
    bare = context_breakdown(messages)
    with_pinned = context_breakdown(messages, memory_chars=1200, repo_map_tokens=900)

    assert bare.tokens("memory") == 0
    assert bare.tokens("repo map") == 0
    assert with_pinned.tokens("memory") == 300  # 1200 chars / 4
    assert with_pinned.tokens("repo map") == 900  # handed in as tokens, round-trips
    assert with_pinned.total_chars > bare.total_chars


def test_the_total_tracks_what_the_status_line_reports() -> None:
    """The two must not disagree about the same transcript.

    ``context_share`` sizes the status bar from ``transcript_tokens``, which rounds up
    per message; this rounds up once over the whole. They therefore differ by at most
    one token per message -- bounded, and asserted, so a future change to either
    estimator cannot quietly open a gap between the bar and the breakdown.
    """
    messages = session()

    breakdown = context_breakdown(messages)

    assert breakdown.total_tokens <= transcript_tokens(messages)
    assert transcript_tokens(messages) - breakdown.total_tokens <= len(messages)


# --------------------------------------------------------------------------- #
# attribution
# --------------------------------------------------------------------------- #


def test_a_tool_result_is_charged_to_tool_results_not_to_the_speaker() -> None:
    """The category that matters most, and the one a naive by-role split gets wrong.

    Tool results arrive in a message whose role is ``tool``, so splitting by role
    alone would work here by accident -- but a tool *call* sits inside an assistant
    message next to prose, and charging it to "assistant" would hide the single
    biggest lever a user has.
    """
    messages = session()

    breakdown = context_breakdown(messages)

    assert breakdown.tokens("tool results") > breakdown.tokens("assistant")
    assert breakdown.tokens("tool calls") > 0
    assert breakdown.tokens("user") > 0
    assert breakdown.tokens("system") > 0


def test_an_error_result_is_still_a_tool_result() -> None:
    """`render_message` prefixes errors differently; the cost is the same category."""
    failed = [
        Message(
            role=Role.TOOL,
            content_blocks=(ToolResultBlock(tool_use_id="c", content="x" * 80, is_error=True),),
        )
    ]

    breakdown = context_breakdown(failed)

    assert breakdown.tokens("tool results") > 0
    assert breakdown.total_chars == len(render_message(failed[0]))


def test_framing_is_named_rather_than_left_as_a_residual() -> None:
    """Role tags and newlines are real tokens, and a transcript of many tiny messages
    spends noticeably on them. An unexplained gap would read as a lossy breakdown."""
    many = [Message(role=Role.USER, content_blocks=(Text(text="ok"),)) for _ in range(50)]

    breakdown = context_breakdown(many)

    assert breakdown.tokens("framing") > 0
    assert breakdown.total_chars == sum(len(render_message(m)) for m in many)


# --------------------------------------------------------------------------- #
# what gets printed
# --------------------------------------------------------------------------- #


def test_ranked_is_heaviest_first_and_drops_empty_categories() -> None:
    messages = session()

    rows = context_breakdown(messages).ranked()

    assert rows == tuple(sorted(rows, key=lambda row: -row[1]))
    assert all(tokens > 0 for _, tokens in rows)
    assert "memory" not in dict(rows)  # nothing was pinned, so it is not printed


def test_ranked_breaks_ties_on_a_fixed_order_not_on_dict_order() -> None:
    """Two categories of equal weight must print in a defined order.

    Built through ``context_breakdown`` this cannot be observed: ``chars`` is always
    seeded from ``BREAKDOWN_CATEGORIES``, so its iteration order is already fixed and
    a stable sort preserves it for ties. Driving the dataclass directly with the keys
    scrambled is the only way to see whether ``ranked`` imposes the order or merely
    inherits it -- and a caller assembling one from a ledger or a resumed session is
    exactly how a scrambled dict would arrive.
    """
    scrambled = ContextBreakdown(chars={"user": 40, "system": 40, "tool calls": 40, "memory": 40})

    names = [name for name, _ in scrambled.ranked()]

    order = list(BREAKDOWN_CATEGORIES)
    assert names == sorted(names, key=order.index)
    assert names == ["memory", "tool calls", "user", "system"]


def test_an_empty_session_has_nothing_to_report() -> None:
    """`/cost` prints no breakdown at all rather than a table of zeros."""
    assert context_breakdown([]).ranked() == ()
    assert context_breakdown([]).total_tokens == 0


def test_share_is_against_the_window_when_there_is_one() -> None:
    messages = session()

    sized = context_breakdown(messages, repo_map_tokens=1_000, context_window=10_000)

    assert sized.share("repo map") == pytest.approx(0.1)


def test_share_falls_back_to_a_fraction_of_the_total_with_no_window() -> None:
    """A window of 0 means "unknown", not "empty" -- so the rows still mean something."""
    messages = session()

    unsized = context_breakdown(messages, repo_map_tokens=1_000)

    assert 0.0 < unsized.share("repo map") < 1.0
    assert sum(unsized.share(name) for name in BREAKDOWN_CATEGORIES) == pytest.approx(1.0)
