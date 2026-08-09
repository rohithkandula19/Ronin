"""Cache-aware assembly: the prefix never moves, and we can prove whether it hit.

Prompt caching is a prefix match, so the tests that matter are the ones asserting
that adding a turn, or repairing a call, or asking a different question leaves the
front of the request byte-identical. A "caching enabled" flag proves nothing; a
stable fingerprint across ten turns does.
"""
from __future__ import annotations

from ronin.core.types import Message, Role, Text, ToolSpec
from ronin.providers import Capabilities, StablePrefix, Usage, assemble, extend
from ronin.providers.assembly import CacheStats

CACHING = Capabilities(
    native_tools=True,
    parallel_tools=True,
    prompt_cache=True,
    thinking=True,
    max_context=200_000,
    vision=True,
)
NO_CACHE = Capabilities(
    native_tools=True,
    parallel_tools=True,
    prompt_cache=False,
    thinking=False,
    max_context=32_768,
    vision=False,
)

TOOLS = (
    ToolSpec(name="read_file", description="Read a file."),
    ToolSpec(name="search", description="Search the repo."),
)
PREFIX = StablePrefix(system="You are Ronin.", tools=TOOLS, repo_map="src/\n  main.py")


def user(text: str) -> Message:
    return Message(role=Role.USER, content_blocks=(Text(text),))


# --------------------------------------------------------------------------- #
# The prefix does not move
# --------------------------------------------------------------------------- #


def test_the_fingerprint_is_stable_across_identical_prefixes() -> None:
    other = StablePrefix(system="You are Ronin.", tools=TOOLS, repo_map="src/\n  main.py")
    assert PREFIX.fingerprint() == other.fingerprint()


def test_adding_turns_does_not_change_the_prefix_fingerprint() -> None:
    first = assemble(PREFIX, [user("hi")], model="m", capabilities=CACHING)
    tenth = first
    for index in range(9):
        tenth = extend(tenth, [user(f"turn {index}")])
    assert tenth.metadata["prefix_fingerprint"] == first.metadata["prefix_fingerprint"]
    assert tenth.system == first.system
    assert tenth.prefix == first.prefix
    assert tenth.tools == first.tools
    assert len(tenth.messages) == 10


def test_reordering_the_tool_list_changes_the_fingerprint() -> None:
    """It breaks the cache, so a fingerprint that ignored order would lie."""
    swapped = StablePrefix(system=PREFIX.system, tools=TOOLS[::-1], repo_map=PREFIX.repo_map)
    assert swapped.fingerprint() != PREFIX.fingerprint()


def test_changing_the_repo_map_changes_the_fingerprint() -> None:
    moved = StablePrefix(system=PREFIX.system, tools=TOOLS, repo_map="src/\n  other.py")
    assert moved.fingerprint() != PREFIX.fingerprint()


def test_changing_a_tool_description_changes_the_fingerprint() -> None:
    reworded = StablePrefix(
        system=PREFIX.system,
        tools=(ToolSpec(name="read_file", description="Reads a file!"), TOOLS[1]),
        repo_map=PREFIX.repo_map,
    )
    assert reworded.fingerprint() != PREFIX.fingerprint()


def test_the_prefix_is_frozen_so_it_cannot_drift_under_a_caller() -> None:
    import dataclasses

    assert PREFIX.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert dataclasses.replace(PREFIX, system="other").system == "other"
    assert PREFIX.system == "You are Ronin."


def test_a_list_of_tools_is_rejected_because_a_list_can_be_reordered_in_place() -> None:
    import pytest

    with pytest.raises(TypeError, match="must be a tuple"):
        StablePrefix(tools=[TOOLS[0]])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The cache marker
# --------------------------------------------------------------------------- #


def test_the_marker_points_at_the_last_stable_block() -> None:
    req = assemble(PREFIX, [user("hi")], model="m", capabilities=CACHING)
    # system + repo map + tools = three blocks, so the last index is 2.
    assert req.cache_marker == 2


def test_the_marker_is_absent_when_the_model_cannot_cache() -> None:
    req = assemble(PREFIX, [user("hi")], model="m", capabilities=NO_CACHE)
    assert req.cache_marker == -1


def test_the_marker_is_absent_when_there_is_no_stable_prefix_at_all() -> None:
    req = assemble(StablePrefix(), [user("hi")], model="m", capabilities=CACHING)
    assert req.cache_marker == -1


def test_the_marker_counts_only_the_blocks_that_exist() -> None:
    req = assemble(
        StablePrefix(system="just a system prompt"),
        [user("hi")],
        model="m",
        capabilities=CACHING,
    )
    assert req.cache_marker == 0


# --------------------------------------------------------------------------- #
# Sampling knobs
# --------------------------------------------------------------------------- #


def test_a_thinking_budget_is_dropped_for_a_model_that_cannot_think() -> None:
    req = assemble(
        PREFIX, [user("hi")], model="m", capabilities=NO_CACHE, thinking_budget=4000
    )
    assert req.thinking_budget == 0


def test_a_thinking_budget_survives_for_a_model_that_can() -> None:
    req = assemble(
        PREFIX, [user("hi")], model="m", capabilities=CACHING, thinking_budget=4000
    )
    assert req.thinking_budget == 4000


def test_messages_are_frozen_into_a_tuple() -> None:
    req = assemble(PREFIX, [user("a"), user("b")], model="m", capabilities=CACHING)
    assert isinstance(req.messages, tuple)


def test_extend_appends_without_touching_anything_stable() -> None:
    first = assemble(PREFIX, [user("a")], model="m", capabilities=CACHING, max_tokens=99)
    second = extend(first, [user("b")])
    assert [m.text for m in second.messages] == ["a", "b"]
    assert second.max_tokens == 99
    assert second.cache_marker == first.cache_marker


# --------------------------------------------------------------------------- #
# Measuring the hit rate
# --------------------------------------------------------------------------- #


def test_the_hit_rate_is_measured_in_tokens_not_requests() -> None:
    """A 40-token miss is nothing like a 30k-token miss."""
    stats = CacheStats()
    stats.record(Usage(input_tokens=30_000, cache_read_tokens=0))
    stats.record(Usage(input_tokens=40, cache_read_tokens=30_000))
    # Per-request this is 50%. In tokens it is far better than that.
    assert stats.hit_rate > 0.49
    assert stats.requests == 2


def test_an_empty_stats_reports_zero_rather_than_dividing_by_zero() -> None:
    assert CacheStats().hit_rate == 0.0


def test_a_perfect_hit_rate_is_one() -> None:
    stats = CacheStats()
    stats.record(Usage(cache_read_tokens=1000))
    assert stats.hit_rate == 1.0


def test_cache_writes_count_against_the_hit_rate() -> None:
    """A write is money spent to maybe save money later; it is not a hit."""
    stats = CacheStats()
    stats.record(Usage(cache_write_tokens=1000))
    assert stats.hit_rate == 0.0
    assert stats.written_tokens == 1000


def test_a_prefix_seen_once_is_flagged_as_written_and_never_read() -> None:
    """The commonest way caching costs money instead of saving it."""
    stats = CacheStats()
    stats.record(Usage(cache_write_tokens=500), fingerprint="aaa")
    stats.record(Usage(cache_read_tokens=500), fingerprint="bbb")
    stats.record(Usage(cache_read_tokens=500), fingerprint="bbb")
    assert stats.write_only_prefixes == ("aaa",)


def test_an_unfingerprinted_request_is_still_counted() -> None:
    stats = CacheStats()
    stats.record(Usage(input_tokens=10))
    assert stats.requests == 1
    assert stats.fingerprints == {}


def test_the_summary_reports_all_three_token_buckets() -> None:
    stats = CacheStats()
    stats.record(Usage(input_tokens=10, cache_read_tokens=90, cache_write_tokens=5))
    summary = stats.summary()
    assert "90" in summary
    assert "10" in summary
    assert "1 request" in summary


# --------------------------------------------------------------------------- #
# Request validation
# --------------------------------------------------------------------------- #


def test_a_request_needs_a_model() -> None:
    import pytest

    from ronin.providers import ModelRequest

    with pytest.raises(ValueError, match="model is required"):
        ModelRequest(model="")


def test_a_request_rejects_a_list_of_messages() -> None:
    import pytest

    from ronin.providers import ModelRequest

    with pytest.raises(TypeError, match="messages must be a tuple"):
        ModelRequest(model="m", messages=[user("a")])  # type: ignore[arg-type]


def test_a_request_rejects_a_list_of_tools() -> None:
    import pytest

    from ronin.providers import ModelRequest

    with pytest.raises(TypeError, match="tools must be a tuple"):
        ModelRequest(model="m", tools=[TOOLS[0]])  # type: ignore[arg-type]


def test_a_request_rejects_a_non_positive_token_cap() -> None:
    import pytest

    from ronin.providers import ModelRequest

    with pytest.raises(ValueError, match="max_tokens must be positive"):
        ModelRequest(model="m", max_tokens=0)


def test_a_request_rejects_a_negative_thinking_budget() -> None:
    import pytest

    from ronin.providers import ModelRequest

    with pytest.raises(ValueError, match="thinking_budget must be >= 0"):
        ModelRequest(model="m", thinking_budget=-1)


def test_with_messages_keeps_everything_stable() -> None:
    req = assemble(PREFIX, [user("a")], model="m", capabilities=CACHING)
    swapped = req.with_messages([user("b")])
    assert swapped.system == req.system
    assert swapped.tools == req.tools
    assert swapped.cache_marker == req.cache_marker
    assert [m.text for m in swapped.messages] == ["b"]


def test_with_system_replaces_only_the_system_prompt() -> None:
    req = assemble(PREFIX, [user("a")], model="m", capabilities=CACHING)
    shimmed = req.with_system("rewritten")
    assert shimmed.system == "rewritten"
    assert shimmed.messages == req.messages
