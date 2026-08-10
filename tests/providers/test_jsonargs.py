"""Unit tests for the argument-repair pipeline.

Two properties matter more than any individual case:

* **Determinism.** Every case is asserted to produce the same result twice, because
  the golden fixture tests are worthless if this is not stable.
* **No silent repair.** Whenever a repair fires, the note is asserted. A pipeline
  that fixes things quietly is one that will eventually fix something into the
  wrong shape and nobody will know which pass did it.
"""

from __future__ import annotations

import pytest

from ronin.providers.jsonargs import (
    ArgsParse,
    close_truncated,
    find_json_objects,
    parse_arguments,
    python_literals_to_json,
    remove_trailing_commas,
    single_to_double_quotes,
    strip_fence,
)

# --------------------------------------------------------------------------- #
# The happy path records nothing
# --------------------------------------------------------------------------- #


def test_valid_json_parses_with_no_repairs() -> None:
    result = parse_arguments('{"path": "a.py", "line": 3}')
    assert result.ok
    assert result.value == {"path": "a.py", "line": 3}
    assert result.repairs == ()


def test_a_mapping_passes_through_untouched() -> None:
    result = parse_arguments({"path": "a.py"})
    assert result.ok
    assert result.value == {"path": "a.py"}
    assert result.repairs == ()


def test_none_and_blank_are_an_argument_less_call_not_an_error() -> None:
    for raw in (None, "", "   ", "\n"):
        result = parse_arguments(raw)
        assert result.ok, raw
        assert result.value == {}


def test_a_non_string_non_mapping_is_rejected_with_its_type_named() -> None:
    result = parse_arguments(42)
    assert not result.ok
    assert "int" in result.error


# --------------------------------------------------------------------------- #
# Each repair, in isolation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('```tool_call\n{"a": 1}```', {"a": 1}),
    ],
)
def test_fenced_payloads_are_unwrapped(raw: str, expected: dict[str, object]) -> None:
    result = parse_arguments(raw)
    assert result.ok
    assert result.value == expected
    assert "stripped_fence" in result.repairs


def test_a_fence_with_no_closing_marker_is_left_alone() -> None:
    assert strip_fence('```json\n{"a": 1}') == '```json\n{"a": 1}'


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1,}', '{"a": 1}'),
        ('{"a": [1, 2,]}', '{"a": [1, 2]}'),
        ('{"a": 1 , }', '{"a": 1 }'),
        ('{"a": {"b": 2,},}', '{"a": {"b": 2}}'),
    ],
)
def test_trailing_commas_are_removed(raw: str, expected: str) -> None:
    assert remove_trailing_commas(raw) == expected


def test_a_comma_inside_a_string_value_is_never_touched() -> None:
    """The classic regex bug: ``", }"`` inside a value is legal content."""
    raw = '{"msg": "hello, }", "n": 1}'
    assert remove_trailing_commas(raw) == raw
    assert parse_arguments(raw).value == {"msg": "hello, }", "n": 1}


def test_an_escaped_quote_does_not_end_the_string() -> None:
    raw = '{"msg": "she said \\", }\\"", "n": 1}'
    assert parse_arguments(raw).value["n"] == 1


def test_python_literals_become_json_literals() -> None:
    result = parse_arguments('{"a": None, "b": True, "c": False}')
    assert result.ok
    assert result.value == {"a": None, "b": True, "c": False}
    assert "python_literals" in result.repairs


def test_the_word_none_inside_a_string_survives() -> None:
    assert python_literals_to_json('{"a": "None"}') == '{"a": "None"}'


def test_a_word_containing_true_is_not_rewritten() -> None:
    assert python_literals_to_json('{"a": Truest}') == '{"a": Truest}'


def test_single_quotes_are_converted_when_unambiguous() -> None:
    result = parse_arguments("{'path': 'a.py'}")
    assert result.ok
    assert result.value == {"path": "a.py"}
    assert "single_quotes" in result.repairs


def test_single_quotes_are_refused_when_double_quotes_are_also_present() -> None:
    """An apostrophe inside a value cannot be told from a delimiter."""
    raw = '{"msg": "it\'s fine"}'
    assert single_to_double_quotes(raw) == raw


# --------------------------------------------------------------------------- #
# Truncation — the case that had a real bug
# --------------------------------------------------------------------------- #


def test_a_truncated_string_value_is_closed_and_kept() -> None:
    result = parse_arguments('{"path": "a.py", "content": "def f(')
    assert result.ok
    assert result.value == {"path": "a.py", "content": "def f("}
    assert "truncated" in result.repairs


def test_a_truncated_key_is_dropped_rather_than_closed_into_nonsense() -> None:
    result = parse_arguments('{"path": "a.py", "conte')
    assert result.ok
    assert result.value == {"path": "a.py"}


def test_a_key_with_a_colon_and_no_value_is_dropped() -> None:
    result = parse_arguments('{"path": "a.py", "content":')
    assert result.ok
    assert result.value == {"path": "a.py"}


def test_a_truncated_nested_structure_is_closed_in_the_right_order() -> None:
    result = parse_arguments('{"a": {"b": [1, 2')
    assert result.ok
    assert result.value == {"a": {"b": [1, 2]}}


def test_a_dangling_escape_does_not_swallow_the_closing_quote() -> None:
    result = parse_arguments('{"path": "a\\')
    assert result.ok
    assert result.value == {"path": "a"}


def test_truncation_of_the_very_first_key_yields_an_empty_object() -> None:
    result = parse_arguments('{"pa')
    assert result.ok
    assert result.value == {}


def test_close_truncated_is_a_no_op_on_complete_json() -> None:
    assert close_truncated('{"a": 1}') == '{"a": 1}'


# --------------------------------------------------------------------------- #
# Shapes that are not an object
# --------------------------------------------------------------------------- #


def test_double_encoded_json_is_unwrapped_once() -> None:
    result = parse_arguments('"{\\"path\\": \\"a.py\\"}"')
    assert result.ok
    assert result.value == {"path": "a.py"}
    assert "double_encoded" in result.repairs


def test_a_single_element_list_is_unwrapped() -> None:
    result = parse_arguments('[{"path": "a.py"}]')
    assert result.ok
    assert result.value == {"path": "a.py"}
    assert "unwrapped_single_element_list" in result.repairs


def test_a_multi_element_list_is_rejected_rather_than_guessed() -> None:
    result = parse_arguments('[{"a": 1}, {"b": 2}]')
    assert not result.ok
    assert "must be a JSON object" in result.error


def test_a_bare_scalar_is_rejected() -> None:
    result = parse_arguments("42")
    assert not result.ok
    assert "int" in result.error


def test_unrecoverable_input_reports_what_was_tried() -> None:
    result = parse_arguments("this is not json at all {{{")
    assert not result.ok
    assert result.raw == "this is not json at all {{{"
    assert "no repair recovered them" in result.error


# --------------------------------------------------------------------------- #
# Determinism and invariants
# --------------------------------------------------------------------------- #

CASES = (
    '{"a": 1}',
    '{"a": 1,}',
    '```json\n{"a": 1}\n```',
    "{'a': 1}",
    '{"a": None}',
    '{"path": "a.py", "content": "def f(',
    '{"path": "a.py", "conte',
    "not json",
    '[{"a": 1}]',
    '"{\\"a\\": 1}"',
)


@pytest.mark.parametrize("raw", CASES)
def test_parsing_is_deterministic(raw: str) -> None:
    first = parse_arguments(raw)
    second = parse_arguments(raw)
    assert first == second


@pytest.mark.parametrize("raw", CASES)
def test_the_raw_payload_is_always_preserved_verbatim(raw: str) -> None:
    """The repair message quotes this back to the model; it must not be cleaned."""
    assert parse_arguments(raw).raw == raw


def test_a_failed_parse_must_say_why() -> None:
    with pytest.raises(ValueError, match="must say why"):
        ArgsParse(ok=False)


def test_a_successful_parse_cannot_carry_an_error() -> None:
    with pytest.raises(ValueError, match="cannot be ok=True"):
        ArgsParse(ok=True, error="boom")


def test_repairs_must_be_a_tuple() -> None:
    with pytest.raises(TypeError, match="must be a tuple"):
        ArgsParse(ok=True, repairs=["a"])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Finding objects in prose
# --------------------------------------------------------------------------- #


def test_multiple_objects_in_one_blob_are_found_separately() -> None:
    found = find_json_objects('noise {"a": 1} more {"b": {"c": 2}} tail')
    assert found == ('{"a": 1}', '{"b": {"c": 2}}')


def test_a_brace_inside_a_string_does_not_open_an_object() -> None:
    assert find_json_objects('{"a": "}{"}') == ('{"a": "}{"}',)


def test_an_unbalanced_object_is_not_returned() -> None:
    assert find_json_objects('{"a": 1') == ()


def test_no_objects_in_plain_prose() -> None:
    assert find_json_objects("just a sentence") == ()
