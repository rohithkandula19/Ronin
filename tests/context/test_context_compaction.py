"""Compaction, with the pairing invariant asserted everywhere it could break.

The two tests that matter most are at the bottom: a 200-turn scripted session
compacted repeatedly, which must still be able to answer "what file did we edit in
turn 3", and the boundary case where a tool call and its result straddle the
tail — the split that produces a provider 400 on the *following* turn, where the
traceback is useless.
"""
from __future__ import annotations

import pytest
from context_harness import (
    assistant,
    fake_summarizer,
    scripted_session,
    tool_results,
    transcript_text,
    user,
    write_call,
)

from ronin.context.compaction import (
    COMPACTION_KEY,
    PATH_ARGUMENT_KEYS,
    PINNED_KEY,
    SUMMARY_SECTIONS,
    CompactionPolicy,
    build_summary_prompt,
    compact,
    file_path_from_arguments,
    latest_tool_result_per_path,
    maybe_compact,
    message_tokens,
    missing_sections,
    plan_compaction,
    repair_pairing,
    should_compact,
    transcript_tokens,
)
from ronin.core.types import (
    Message,
    Role,
    Text,
    ToolResultBlock,
    ToolUse,
    unpaired_tool_uses,
)

TINY = CompactionPolicy(context_window=400)


# --------------------------------------------------------------------------- #
# policy + trigger
# --------------------------------------------------------------------------- #


def test_the_trigger_is_eighty_percent_of_the_window_by_default() -> None:
    assert CompactionPolicy(context_window=100_000).trigger_tokens == 80_000


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("context_window", 0),
        ("trigger_fraction", 0.0),
        ("trigger_fraction", 1.5),
        ("pinned_tail_turns", 0),
        ("max_retained_chars", 0),
        ("max_retained_paths", 0),
    ],
)
def test_a_nonsense_policy_is_rejected_at_construction(field: str, value: float) -> None:
    kwargs: dict[str, float] = {"context_window": 1000}
    kwargs[field] = value
    with pytest.raises(ValueError, match=field):
        CompactionPolicy(**kwargs)  # type: ignore[arg-type]


def test_a_short_transcript_does_not_trigger() -> None:
    assert not should_compact([user("hello")], policy=CompactionPolicy(context_window=1000))


def test_context_outside_the_transcript_counts_toward_the_trigger() -> None:
    """A 30k-token repo map in the stable prefix is 30k tokens the provider sees."""
    messages = [user("hi")]
    policy = CompactionPolicy(context_window=1000)
    assert not should_compact(messages, policy=policy)
    assert should_compact(messages, policy=policy, pinned_prefix_tokens=800)


async def test_maybe_compact_leaves_a_small_transcript_alone() -> None:
    messages = [user("hi"), assistant("hello")]
    result = await maybe_compact(
        messages, policy=CompactionPolicy(context_window=100_000), summarizer=fake_summarizer
    )
    assert not result.compacted
    assert result.messages == tuple(messages)
    assert result.marker == ""


# --------------------------------------------------------------------------- #
# path extraction
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"path": "src/a.py"}, "src/a.py"),
        ({"file_path": "src/a.py"}, "src/a.py"),
        ({"notebook_path": "nb.ipynb"}, "nb.ipynb"),
        ({"filename": "a.txt"}, "a.txt"),
        ({"path": "./src/a.py"}, "src/a.py"),
        ({"path": "././src/a.py"}, "src/a.py"),
        ({"path": "  src/a.py  "}, "src/a.py"),
        # file_path wins over path when both are present
        ({"path": "b.py", "file_path": "a.py"}, "a.py"),
        ({"pattern": "def foo", "glob": "*.py"}, None),
        ({"path": ""}, None),
        ({"path": 3}, None),
        ({}, None),
    ],
)
def test_the_path_argument_mapping_is_explicit(
    arguments: dict[str, object], expected: str | None
) -> None:
    assert file_path_from_arguments(arguments) == expected


def test_the_path_key_list_is_ordered_by_priority() -> None:
    assert PATH_ARGUMENT_KEYS[0] == "file_path"
    assert "path" in PATH_ARGUMENT_KEYS


def test_the_latest_result_per_path_is_the_one_kept() -> None:
    messages = [
        assistant("", write_call("c1", "a.py", "first")),
        tool_results(ToolResultBlock(tool_use_id="c1", content="wrote first")),
        assistant("", write_call("c2", "a.py", "second")),
        tool_results(ToolResultBlock(tool_use_id="c2", content="wrote second")),
        assistant("", write_call("c3", "b.py", "other")),
        tool_results(ToolResultBlock(tool_use_id="c3", content="wrote other")),
    ]
    latest = latest_tool_result_per_path(messages)
    assert set(latest) == {"a.py", "b.py"}
    assert latest["a.py"][1].content == "wrote second"


def test_an_unanswered_call_is_not_retainable() -> None:
    """Re-emitting a call with no result is exactly how pairing breaks."""
    messages = [assistant("", write_call("c1", "a.py", "x"))]
    assert latest_tool_result_per_path(messages) == {}


def test_two_spellings_of_one_path_collapse_to_one_retained_entry() -> None:
    messages = [
        assistant("", write_call("c1", "./src/a.py", "x")),
        tool_results(ToolResultBlock(tool_use_id="c1", content="one")),
        assistant("", write_call("c2", "src/a.py", "y")),
        tool_results(ToolResultBlock(tool_use_id="c2", content="two")),
    ]
    assert list(latest_tool_result_per_path(messages)) == ["src/a.py"]


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #


def test_the_leading_system_messages_are_pinned() -> None:
    messages = [
        Message(role=Role.SYSTEM, content_blocks=(Text("system prompt"),)),
        Message(role=Role.SYSTEM, content_blocks=(Text("repo map"),)),
        *scripted_session(10)[1:],
    ]
    plan = plan_compaction(messages, policy=TINY)
    assert plan.head_end == 2


def test_a_mid_transcript_system_message_is_not_pinned() -> None:
    """`core.loop` inserts system-role stall nudges; pinning those keeps scaffolding."""
    messages = scripted_session(10)
    messages.insert(
        5, Message(role=Role.SYSTEM, content_blocks=(Text("stall nudge"),))
    )
    assert plan_compaction(messages, policy=TINY).head_end == 1


def test_the_last_three_turns_are_never_folded() -> None:
    messages = scripted_session(10)
    plan = plan_compaction(messages, policy=TINY)
    tail = messages[plan.tail_start :]
    user_turns = [m for m in tail if m.role is Role.USER]
    assert len(user_turns) == 3
    assert user_turns[0].text.startswith("turn 8")


def test_nothing_is_folded_when_there_are_only_three_turns() -> None:
    messages = scripted_session(3)
    plan = plan_compaction(messages, policy=TINY)
    assert not plan.has_middle


async def test_a_transcript_with_nothing_to_fold_is_returned_unchanged() -> None:
    messages = scripted_session(2)
    result = await compact(messages, policy=TINY, summarizer=fake_summarizer)
    assert not result.compacted
    assert result.messages == tuple(messages)


def test_a_tool_pair_straddling_the_tail_boundary_is_not_split() -> None:
    """An orphan *result* in the tail is a 400 too, and `unpaired_tool_uses` misses it."""
    messages = [
        Message(role=Role.SYSTEM, content_blocks=(Text("sys"),)),
        *[
            block
            for turn in range(1, 6)
            for block in (
                user(f"turn {turn}"),
                assistant("", write_call(f"c{turn}", f"f{turn}.py", "x")),
                tool_results(ToolResultBlock(tool_use_id=f"c{turn}", content="ok")),
            )
        ],
    ]
    # Move a result so its call sits one message before the natural boundary.
    plan = plan_compaction(messages, policy=TINY)
    tail = messages[plan.tail_start :]
    tail_result_ids = {
        block.tool_use_id
        for message in tail
        for block in message.content_blocks
        if isinstance(block, ToolResultBlock)
    }
    tail_call_ids = {
        block.id
        for message in tail
        for block in message.content_blocks
        if isinstance(block, ToolUse)
    }
    assert tail_result_ids <= tail_call_ids


def test_a_pinned_message_in_the_middle_survives_the_fold() -> None:
    messages = scripted_session(10)
    messages.insert(
        4,
        Message(
            role=Role.USER,
            content_blocks=(Text("RONIN.md says: always run make lint"),),
            metadata={PINNED_KEY: True},
        ),
    )
    plan = plan_compaction(messages, policy=TINY)
    assert plan.hoisted


# --------------------------------------------------------------------------- #
# the fold
# --------------------------------------------------------------------------- #


async def test_compaction_folds_the_middle_and_marks_the_transcript() -> None:
    messages = scripted_session(20)
    result = await compact(messages, policy=TINY, summarizer=fake_summarizer)
    assert result.compacted
    assert result.folded_messages > 0
    assert f"{result.folded_messages} message(s) folded" in result.marker
    assert f"~{result.token_estimate_after:,} tokens" in result.marker
    assert "estimated at 4 chars/token" in result.marker
    marker_in_transcript = any(
        result.marker in block.text
        for message in result.messages
        for block in message.content_blocks
        if isinstance(block, Text)
    )
    assert marker_in_transcript


async def test_the_summary_message_is_flagged_in_its_metadata() -> None:
    result = await compact(scripted_session(20), policy=TINY, summarizer=fake_summarizer)
    summaries = [m for m in result.messages if m.metadata.get(COMPACTION_KEY)]
    assert len(summaries) == 1
    assert "## Failed" in summaries[0].text


async def test_the_summary_prompt_asks_for_exactly_five_sections() -> None:
    captured: list[str] = []

    async def capturing(prompt: str) -> str:
        captured.append(prompt)
        return "## Goal\nx\n"

    await compact(scripted_session(20), policy=TINY, summarizer=capturing)
    for section in SUMMARY_SECTIONS:
        assert section in captured[0]
    assert "turn 4" in captured[0]


async def test_a_summary_missing_sections_is_reported_not_rewritten() -> None:
    async def partial(prompt: str) -> str:
        return "## Goal\nship it\n"

    result = await compact(scripted_session(20), policy=TINY, summarizer=partial)
    assert result.summary == "## Goal\nship it"
    assert result.missing_sections == SUMMARY_SECTIONS[1:]


async def test_a_failing_summarizer_falls_back_to_a_local_digest_and_says_so() -> None:
    """A session at 80% that cannot compact dies on its next request."""

    async def broken(prompt: str) -> str:
        raise RuntimeError("fast model unreachable")

    result = await compact(scripted_session(20), policy=TINY, summarizer=broken)
    assert result.compacted
    assert "RuntimeError: fast model unreachable" in result.summarizer_error
    assert "summarizer FAILED" in result.marker
    assert missing_sections(result.summary) == ()
    assert "src/turn3.py" in result.summary
    assert unpaired_tool_uses(result.messages) == ()


async def test_an_empty_summary_is_treated_as_a_failure() -> None:
    async def empty(prompt: str) -> str:
        return "   \n"

    result = await compact(scripted_session(20), policy=TINY, summarizer=empty)
    assert result.summarizer_error == "summarizer returned empty text"


async def test_the_fold_reduces_the_token_estimate() -> None:
    messages = scripted_session(60)
    result = await compact(messages, policy=TINY, summarizer=fake_summarizer)
    assert result.token_estimate_before == transcript_tokens(messages)
    assert result.token_estimate_after == transcript_tokens(result.messages)
    assert result.token_estimate_after < result.token_estimate_before


async def test_retained_results_are_capped_per_path_when_asked() -> None:
    messages = [
        Message(role=Role.SYSTEM, content_blocks=(Text("sys"),)),
        user("turn 1"),
        assistant("", write_call("c1", "big.py", "x")),
        tool_results(ToolResultBlock(tool_use_id="c1", content="\n".join("line" for _ in range(500)))),
        *scripted_session(5)[1:],
    ]
    policy = CompactionPolicy(context_window=400, max_retained_chars=200)
    result = await compact(messages, policy=policy, summarizer=fake_summarizer)
    retained = [
        block.content
        for message in result.messages
        for block in message.content_blocks
        if isinstance(block, ToolResultBlock) and message.metadata.get("retained_path")
    ]
    big = [content for content in retained if "lines elided" in content]
    assert big and len(big[0]) <= 200


async def test_bounding_the_retained_path_count_keeps_the_most_recent() -> None:
    policy = CompactionPolicy(context_window=400, max_retained_paths=2)
    result = await compact(scripted_session(20), policy=policy, summarizer=fake_summarizer)
    assert len(result.retained_paths) == 2
    assert "src/turn3.py" not in result.retained_paths


async def test_retention_can_be_switched_off_entirely() -> None:
    policy = CompactionPolicy(context_window=400, retain_tool_results_per_path=False)
    result = await compact(scripted_session(20), policy=policy, summarizer=fake_summarizer)
    assert result.retained_paths == ()
    assert unpaired_tool_uses(result.messages) == ()


# --------------------------------------------------------------------------- #
# the pairing invariant
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("turns", [4, 5, 9, 17, 40, 101])
async def test_compaction_never_leaves_an_unpaired_tool_call(turns: int) -> None:
    result = await compact(scripted_session(turns), policy=TINY, summarizer=fake_summarizer)
    assert unpaired_tool_uses(result.messages) == ()
    assert result.dropped_blocks == 0


async def test_every_retained_result_is_answered_by_a_call_in_the_same_transcript() -> None:
    result = await compact(scripted_session(30), policy=TINY, summarizer=fake_summarizer)
    calls = {
        block.id
        for message in result.messages
        for block in message.content_blocks
        if isinstance(block, ToolUse)
    }
    results = {
        block.tool_use_id
        for message in result.messages
        for block in message.content_blocks
        if isinstance(block, ToolResultBlock)
    }
    assert results == calls


async def test_a_retained_result_immediately_follows_its_call() -> None:
    """Providers require the result in the message right after the call."""
    result = await compact(scripted_session(30), policy=TINY, summarizer=fake_summarizer)
    messages = list(result.messages)
    for index, message in enumerate(messages):
        for block in message.content_blocks:
            if isinstance(block, ToolUse):
                following = messages[index + 1]
                assert any(
                    isinstance(answer, ToolResultBlock) and answer.tool_use_id == block.id
                    for answer in following.content_blocks
                )


def test_repair_pairing_drops_an_orphan_call_and_counts_it() -> None:
    messages = [
        assistant("", write_call("c1", "a.py", "x")),
        assistant("", write_call("c2", "b.py", "y")),
        tool_results(ToolResultBlock(tool_use_id="c2", content="ok")),
    ]
    repaired, dropped = repair_pairing(messages)
    assert dropped == 1
    assert unpaired_tool_uses(repaired) == ()
    assert len(repaired) == 2


def test_repair_pairing_drops_an_orphan_result() -> None:
    messages = [tool_results(ToolResultBlock(tool_use_id="ghost", content="ok"))]
    repaired, dropped = repair_pairing(messages)
    assert dropped == 1
    assert repaired == []


async def test_a_transcript_that_reuses_a_tool_id_folds_nothing_rather_than_mangling() -> None:
    """Duplicate ids are already a provider 400; compaction refuses and reports it."""
    duplicated = [*scripted_session(8), *scripted_session(4)[1:]]
    result = await compact(duplicated, policy=TINY, summarizer=fake_summarizer)
    assert not result.compacted
    assert result.still_over_trigger
    assert result.messages == tuple(duplicated)


def test_repair_pairing_leaves_a_healthy_transcript_untouched() -> None:
    messages = scripted_session(4)
    repaired, dropped = repair_pairing(messages)
    assert dropped == 0
    assert repaired == messages


# --------------------------------------------------------------------------- #
# estimation
# --------------------------------------------------------------------------- #


def test_tool_calls_and_arguments_count_toward_the_estimate() -> None:
    """A prose-only estimator under-counts every long coding session."""
    prose = Message(role=Role.ASSISTANT, content_blocks=(Text("ok"),))
    with_call = assistant("ok", write_call("c1", "src/a.py", "x" * 100))
    assert message_tokens(with_call) > message_tokens(prose)


def test_the_summary_prompt_contains_the_transcript() -> None:
    prompt = build_summary_prompt(scripted_session(3))
    assert "turn 1" in prompt and "turn 3" in prompt
    assert "TRANSCRIPT" in prompt


# --------------------------------------------------------------------------- #
# the 200-turn session — the requirement this module exists for
# --------------------------------------------------------------------------- #


async def test_a_200_turn_session_can_still_answer_what_we_edited_in_turn_3() -> None:
    """Compact repeatedly, as a real session would, and check turn 3 survives.

    Turn 3 wrote ``src/turn3.py`` and nothing touched it again. Per-path retention
    is what keeps the path *and its content* readable 197 turns later; a summary of
    a diff is not a diff, and this is the assertion that proves the difference.
    """
    messages = scripted_session(200, marked_turn=3, marked_path="src/turn3.py")
    policy = CompactionPolicy(context_window=4000)
    compactions = 0
    for _ in range(12):
        result = await maybe_compact(messages, policy=policy, summarizer=fake_summarizer)
        if not result.compacted:
            break
        compactions += 1
        messages = list(result.messages)
        assert unpaired_tool_uses(messages) == (), "a fold split a tool pair"
        text = transcript_text(messages)
        assert "src/turn3.py" in text, "the turn-3 path was lost"
        assert "SENTINEL turn 3" in text, "the turn-3 file content was lost"
        # More turns arrive on top of the compacted transcript, with fresh ids:
        # reusing a tool-use id is itself a provider 400, so a test that did it
        # would be measuring a broken transcript rather than compaction.
        messages.extend(
            scripted_session(6, marked_turn=0, offset=1000 * compactions)[1:]
        )

    assert compactions >= 3, "the session never actually compacted"
    final = transcript_text(messages)
    assert "src/turn3.py" in final
    assert "SENTINEL turn 3" in final
    assert unpaired_tool_uses(messages) == ()


async def test_repeated_compaction_is_stable_rather_than_growing() -> None:
    """Folding a folded transcript must not re-expand it."""
    messages = list(scripted_session(120))
    policy = CompactionPolicy(context_window=4000)
    first = await compact(messages, policy=policy, summarizer=fake_summarizer)
    second = await compact(list(first.messages), policy=policy, summarizer=fake_summarizer)
    assert second.token_estimate_after <= first.token_estimate_after
    assert unpaired_tool_uses(second.messages) == ()


async def test_a_session_touching_more_files_than_fit_reports_it_rather_than_looping() -> None:
    """The one case compaction cannot fix: retention alone exceeds the trigger."""
    result = await compact(
        scripted_session(200), policy=CompactionPolicy(context_window=4000), summarizer=fake_summarizer
    )
    assert result.compacted
    assert result.still_over_trigger
    bounded = await compact(
        scripted_session(200),
        policy=CompactionPolicy(context_window=4000, max_retained_paths=5),
        summarizer=fake_summarizer,
    )
    assert not bounded.still_over_trigger
