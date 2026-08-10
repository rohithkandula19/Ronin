"""Unit tests for the tool-call accumulator: the shapes, and the id rules.

The accumulator's contract is narrow and load-bearing: fold fragments in any order
and any granularity, then hand back frozen ``ToolUse`` values with ids that are
unique within the response and stable across replays. Everything here is testing
one of those two words — *unique* or *stable*.
"""

from __future__ import annotations

import pytest

from ronin.providers.normalize import (
    SYNTHETIC_PREFIX,
    NormalizedCalls,
    ToolCallAccumulator,
    normalize_one,
    synthetic_id,
)


def acc() -> ToolCallAccumulator:
    return ToolCallAccumulator()


# --------------------------------------------------------------------------- #
# Fragment folding
# --------------------------------------------------------------------------- #


def test_an_empty_accumulator_yields_nothing() -> None:
    result = acc().finish()
    assert result == NormalizedCalls()
    assert result.ok
    assert acc().empty


def test_arguments_split_across_fragments_are_concatenated() -> None:
    a = acc()
    a.add(0, call_id="c1", name="read_file")
    for fragment in ('{"pa', 'th": ', '"a.py"', "}"):
        a.add(0, arguments_fragment=fragment)
    result = a.finish()
    assert result.uses == (result.uses[0],)
    assert result.uses[0].arguments == {"path": "a.py"}


def test_one_character_at_a_time_gives_the_same_answer() -> None:
    payload = '{"path": "a.py", "line": 12}'
    a = acc()
    a.add(0, call_id="c1", name="read_file")
    for char in payload:
        a.add(0, arguments_fragment=char)
    assert a.finish().uses[0].arguments == {"path": "a.py", "line": 12}


def test_the_id_and_name_may_arrive_after_the_arguments() -> None:
    a = acc()
    a.add(0, arguments_fragment='{"path": "a.py"}')
    a.add(0, call_id="c1", name="read_file")
    use = a.finish().uses[0]
    assert (use.id, use.name) == ("c1", "read_file")


def test_a_later_id_overwrites_an_earlier_one() -> None:
    a = acc()
    a.add(0, call_id="first", name="t")
    a.add(0, call_id="second")
    assert a.finish().uses[0].id == "second"


def test_a_name_streamed_in_pieces_is_merged_not_duplicated() -> None:
    a = acc()
    a.add(0, name="read_")
    a.add(0, name="read_file")
    a.add(0, arguments_fragment="{}")
    assert a.finish().uses[0].name == "read_file"


def test_a_name_repeated_identically_is_not_doubled() -> None:
    a = acc()
    a.add(0, name="search")
    a.add(0, name="search")
    a.add(0, arguments_fragment="{}")
    assert a.finish().uses[0].name == "search"


def test_a_genuinely_split_name_is_joined() -> None:
    a = acc()
    a.add(0, name="rea")
    a.add(0, name="d_file")
    a.add(0, arguments_fragment="{}")
    assert a.finish().uses[0].name == "read_file"


def test_calls_come_back_in_first_appearance_order_not_index_order() -> None:
    a = acc()
    a.add(5, call_id="c5", name="second", arguments_fragment="{}")
    a.add(1, call_id="c1", name="first", arguments_fragment="{}")
    assert [use.name for use in a.finish().uses] == ["second", "first"]


def test_add_complete_accepts_a_mapping() -> None:
    a = acc()
    a.add_complete(0, name="read_file", arguments={"path": "a.py"}, call_id="c1")
    assert a.finish().uses[0].arguments == {"path": "a.py"}


def test_a_negative_index_is_rejected_by_the_delta_type() -> None:
    from ronin.providers.types import ToolCallDelta

    with pytest.raises(ValueError, match="must be >= 0"):
        ToolCallDelta(index=-1)


# --------------------------------------------------------------------------- #
# Missing names and ids
# --------------------------------------------------------------------------- #


def test_a_call_with_no_name_is_dropped_loudly() -> None:
    """The one thing worth dropping: there is nothing to route and nothing to fix."""
    a = acc()
    a.add(0, call_id="c1", arguments_fragment='{"path": "a.py"}')
    result = a.finish()
    assert result.uses == ()
    assert result.failed == ()
    assert any("no tool name" in note for note in result.notes)


def test_a_whitespace_only_name_counts_as_no_name() -> None:
    a = acc()
    a.add(0, call_id="c1", name="   ", arguments_fragment="{}")
    assert a.finish().uses == ()


def test_a_missing_id_is_minted_and_the_note_says_so() -> None:
    a = acc()
    a.add(0, name="read_file", arguments_fragment='{"path": "a.py"}')
    result = a.finish()
    assert result.uses[0].id.startswith(f"{SYNTHETIC_PREFIX}_")
    assert any("minted id" in note for note in result.notes)


def test_a_minted_id_is_stable_for_the_same_content() -> None:
    def mint() -> str:
        a = acc()
        a.add(0, name="read_file", arguments_fragment='{"path": "a.py"}')
        return a.finish().uses[0].id

    assert mint() == mint()


def test_a_minted_id_differs_when_the_arguments_differ() -> None:
    assert synthetic_id(0, "t", '{"a": 1}') != synthetic_id(0, "t", '{"a": 2}')


def test_a_minted_id_differs_when_the_index_differs() -> None:
    assert synthetic_id(0, "t", "{}") != synthetic_id(1, "t", "{}")


# --------------------------------------------------------------------------- #
# Collisions — the failure that surfaces a turn late
# --------------------------------------------------------------------------- #


def test_two_calls_sharing_an_id_are_disambiguated() -> None:
    a = acc()
    a.add(0, call_id="call_0", name="read_file", arguments_fragment='{"path": "a.py"}')
    a.add(1, call_id="call_0", name="read_file", arguments_fragment='{"path": "b.py"}')
    result = a.finish()
    ids = [use.id for use in result.uses]
    assert ids == ["call_0", "call_0#2"]
    assert any("collided" in note for note in result.notes)


def test_three_calls_sharing_an_id_all_stay_distinct() -> None:
    a = acc()
    for index in range(3):
        a.add(index, call_id="dup", name="t", arguments_fragment=f'{{"i": {index}}}')
    ids = [use.id for use in a.finish().uses]
    assert ids == ["dup", "dup#2", "dup#3"]
    assert len(set(ids)) == 3


def test_two_identical_calls_still_get_distinct_minted_ids() -> None:
    """Content-derived ids collide for identical calls; the batch pass fixes it."""
    a = acc()
    a.add(0, name="read_file", arguments_fragment='{"path": "a.py"}')
    a.add(1, name="read_file", arguments_fragment='{"path": "a.py"}')
    ids = [use.id for use in a.finish().uses]
    assert len(set(ids)) == 2


# --------------------------------------------------------------------------- #
# Failures are values
# --------------------------------------------------------------------------- #


def test_an_unparseable_call_is_kept_as_a_failure_not_dropped() -> None:
    """Dropping it makes the model look silent, and it retries the same call."""
    a = acc()
    a.add(0, call_id="c1", name="read_file", arguments_fragment="not json {{{")
    result = a.finish()
    assert result.uses == ()
    assert not result.ok
    failure = result.failed[0]
    assert failure.use.id == "c1"
    assert failure.use.name == "read_file"
    assert failure.use.arguments == {}
    assert failure.raw == "not json {{{"
    assert failure.error


def test_a_failed_call_keeps_a_pairable_id_so_the_transcript_stays_sendable() -> None:
    a = acc()
    a.add(0, name="read_file", arguments_fragment="{{{")
    failure = a.finish().failed[0]
    assert failure.use.id


def test_good_and_bad_calls_in_one_batch_are_separated() -> None:
    a = acc()
    a.add(0, call_id="ok", name="read_file", arguments_fragment='{"path": "a.py"}')
    a.add(1, call_id="bad", name="write_file", arguments_fragment="???")
    result = a.finish()
    assert [use.id for use in result.uses] == ["ok"]
    assert [f.use.id for f in result.failed] == ["bad"]


def test_repairs_are_reported_per_tool_name() -> None:
    a = acc()
    a.add(0, call_id="c1", name="read_file", arguments_fragment='{"path": "a.py",}')
    notes = a.finish().notes
    assert any(note.startswith("read_file: ") for note in notes)


# --------------------------------------------------------------------------- #
# The single-call convenience
# --------------------------------------------------------------------------- #


def test_normalize_one_returns_the_use_and_the_parse() -> None:
    use, parsed = normalize_one(name="read_file", arguments='{"path": "a.py"}', call_id="c1")
    assert use is not None
    assert use.id == "c1"
    assert parsed.ok


def test_normalize_one_returns_none_and_the_reason_on_failure() -> None:
    use, parsed = normalize_one(name="read_file", arguments="nope")
    assert use is None
    assert not parsed.ok


def test_normalize_one_mints_an_id_when_none_is_given() -> None:
    use, _ = normalize_one(name="read_file", arguments="{}")
    assert use is not None
    assert use.id.startswith(f"{SYNTHETIC_PREFIX}_")
