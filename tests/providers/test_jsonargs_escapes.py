"""Backslash escapes inside JSON strings, across all four of `jsonargs`' scanners.

`jsonargs.py` hand-rolls string-awareness four separate times — `_scan`,
`remove_trailing_commas`, `python_literals_to_json` and `find_json_objects` — because
each walks the text for a different purpose. Every one of them tracks an `escaped`
flag, and **none of those escape branches had a test**: the four uncovered
`escaped = False` lines were the bulk of what was missing in this module.

That is the dangerous kind of gap, because the failure is silent and it corrupts
rather than rejects. If a scanner mishandles `\\"`, it believes the string ended
early; from there a comma *inside* the value looks like a trailing comma and gets
deleted, or a `}` inside the value looks like the end of the object. The payload
still parses — into the wrong arguments. The module's own docstrings name this as
the bug a regex-based repair classically has, so the escape handling is the thing
that makes the hand-rolled scanners worth their length.

Each test below therefore asserts the *value survives intact*, not merely that the
call did not crash.
"""

from __future__ import annotations

import json

from ronin.providers.jsonargs import (
    _drop_dangling_key,
    _is_word_char,
    _scan,
    _string_start,
    close_truncated,
    find_json_objects,
    parse_arguments,
    python_literals_to_json,
    remove_trailing_commas,
    single_to_double_quotes,
    strip_fence,
)

# --------------------------------------------------------------------------- #
# _scan — the shared structural walk
# --------------------------------------------------------------------------- #


def test_an_escaped_quote_does_not_end_the_string() -> None:
    """`{"a": "say \\"hi\\""}` — if the escaped quotes closed the string, the
    scanner would read `hi` as structure and the brace depth would be wrong."""
    stack, in_string, escaped = _scan(r'{"a": "say \"hi\""}')
    assert stack == []
    assert not in_string
    assert not escaped


def test_an_escaped_backslash_does_not_escape_the_quote_after_it() -> None:
    r"""`"a\\"` is a complete string: the backslash is escaped, so the quote closes.

    Getting this wrong in the other direction is just as bad — the scanner would
    think the payload is still inside a string and `close_truncated` would append
    a quote that corrupts a payload which was already valid.
    """
    stack, in_string, escaped = _scan(r'{"a": "back\\"}')
    assert stack == []
    assert not in_string
    assert not escaped


def test_a_brace_inside_an_escaped_string_is_not_structure() -> None:
    stack, in_string, _ = _scan(r'{"a": "\"}\""}')
    assert stack == []
    assert not in_string


def test_a_dangling_backslash_at_the_end_is_reported() -> None:
    """The flag `close_truncated` needs so it does not append a quote that the
    stray backslash would escape."""
    _, in_string, escaped = _scan('{"a": "x\\')
    assert in_string
    assert escaped


# --------------------------------------------------------------------------- #
# remove_trailing_commas
# --------------------------------------------------------------------------- #


def test_a_comma_inside_a_string_with_escaped_quotes_is_preserved() -> None:
    """The exact corruption this walk exists to prevent.

    A regex would see `", }` and delete the comma, silently rewriting the
    argument's *value*. The comma here is data.
    """
    text = r'{"msg": "he said \"go\", then left",}'
    out = remove_trailing_commas(text)
    assert json.loads(out)["msg"] == 'he said "go", then left'


def test_a_closing_brace_inside_a_string_does_not_trigger_comma_removal() -> None:
    text = r'{"a": 1, "tpl": "{\"x\": 2,}"}'
    out = remove_trailing_commas(text)
    assert json.loads(out)["tpl"] == '{"x": 2,}'


def test_a_real_trailing_comma_next_to_escapes_is_still_removed() -> None:
    text = r'{"path": "a\\b", "flag": true,}'
    parsed = json.loads(remove_trailing_commas(text))
    assert parsed == {"path": "a\\b", "flag": True}


def test_an_escaped_backslash_before_the_closing_quote_is_handled() -> None:
    text = r'{"dir": "C:\\", "n": 1,}'
    parsed = json.loads(remove_trailing_commas(text))
    assert parsed["dir"] == "C:\\"


# --------------------------------------------------------------------------- #
# close_truncated
# --------------------------------------------------------------------------- #


def test_a_stream_cut_inside_an_escaped_sequence_still_closes() -> None:
    """A dropped connection mid-escape. Recovering the arguments that did arrive
    beats discarding a call the model genuinely made."""
    out = close_truncated(r'{"content": "def f():\n    return \"')
    assert json.loads(out)["content"].endswith('"')


def test_a_trailing_lone_backslash_is_dropped_before_closing() -> None:
    out = close_truncated('{"content": "abc\\')
    assert json.loads(out) == {"content": "abc"}


# --------------------------------------------------------------------------- #
# _string_start / _drop_dangling_key
# --------------------------------------------------------------------------- #


def test_string_start_refuses_text_that_does_not_end_in_a_quote() -> None:
    assert _string_start('{"a": 1') is None


def test_string_start_skips_an_escaped_closing_quote() -> None:
    r"""`"a\"` ends with an *escaped* quote, so it is not the delimiter.

    The backslash-parity count is what distinguishes `\"` (escaped, keep looking)
    from `\\"` (escaped backslash, so the quote is real).
    """
    text = r'{"k": "a\"'
    assert _string_start(text) == text.index('"', text.index("k") + 2)


def test_string_start_returns_none_when_no_opening_quote_exists() -> None:
    """Every quote in the text is escaped, so no literal ever opens."""
    assert _string_start(r"\"") is None


def test_a_dangling_key_with_no_resolvable_string_is_left_alone() -> None:
    assert _drop_dangling_key(r"\"") == r"\""


def test_a_dangling_key_is_dropped_but_a_dangling_value_is_kept() -> None:
    assert _drop_dangling_key('{"path": "a.py", "conte"') == '{"path": "a.py"'
    kept = '{"path": "a.py", "content": "def f("'
    assert _drop_dangling_key(kept) == kept


# --------------------------------------------------------------------------- #
# python_literals_to_json
# --------------------------------------------------------------------------- #


def test_the_word_none_survives_inside_a_string_with_escapes() -> None:
    """A token-level rewrite, so a *value* mentioning None is not rewritten."""
    out = python_literals_to_json(r'{"note": "say \"None\" out loud", "x": None}')
    parsed = json.loads(out)
    assert parsed["note"] == 'say "None" out loud'
    assert parsed["x"] is None


def test_python_literals_outside_strings_become_json() -> None:
    parsed = json.loads(
        python_literals_to_json("{'a': None, 'b': True, 'c': False}".replace("'", '"'))
    )
    assert parsed == {"a": None, "b": True, "c": False}


def test_a_literal_glued_to_a_word_is_not_rewritten() -> None:
    """`NoneType` is not `None`. Word-boundary checks on both sides."""
    assert "NoneType" in python_literals_to_json('{"t": NoneType}')


def test_is_word_char_treats_out_of_range_as_not_a_word() -> None:
    """Index -1 is the lookbehind at position 0, so this runs on every payload
    that starts with a literal."""
    assert not _is_word_char("None", -1)
    assert not _is_word_char("None", 4)
    assert _is_word_char("None", 0)


# --------------------------------------------------------------------------- #
# find_json_objects — "multiple calls in one text block"
# --------------------------------------------------------------------------- #


def test_a_brace_inside_an_escaped_string_does_not_split_an_object() -> None:
    """Without escape tracking this returns three fragments instead of one
    object, and the shim reports two bogus failed calls."""
    text = r'{"name": "write", "arguments": {"body": "if (x) {\"y\"}"}}'
    found = find_json_objects(text)
    assert found == (text,)
    assert json.loads(found[0])["arguments"]["body"] == 'if (x) {"y"}'


def test_two_objects_separated_by_prose_are_both_found() -> None:
    found = find_json_objects('call one {"name": "a"} and then {"name": "b"} done')
    assert found == ('{"name": "a"}', '{"name": "b"}')


def test_an_unbalanced_object_yields_nothing_rather_than_a_guess() -> None:
    assert find_json_objects('{"name": "a"') == ()


def test_a_quote_inside_a_string_does_not_open_structure() -> None:
    found = find_json_objects(r'{"a": "he said \"{\" once"}')
    assert len(found) == 1


# --------------------------------------------------------------------------- #
# parse_arguments — the double-encoding floor
# --------------------------------------------------------------------------- #


def test_a_string_that_is_not_json_at_all_is_a_failure_not_a_guess() -> None:
    """`_coerce_object` returning None: a bare word is not arguments, and
    inventing `{"value": "hello"}` here would fabricate a call."""
    result = parse_arguments('"hello"')
    assert not result.ok


def test_a_double_encoded_object_is_unwrapped_and_recorded() -> None:
    result = parse_arguments(json.dumps(json.dumps({"path": "a.py"})))
    assert result.ok
    assert result.value == {"path": "a.py"}
    assert "double_encoded" in result.repairs


def test_a_fenced_payload_with_escapes_parses() -> None:
    fenced = '```json\n{"msg": "a \\"b\\" c"}\n```'
    assert strip_fence(fenced) == '{"msg": "a \\"b\\" c"}'
    result = parse_arguments(fenced)
    assert result.ok
    assert result.value == {"msg": 'a "b" c'}


def test_single_quotes_are_refused_when_a_double_quote_is_present() -> None:
    """Ambiguous: an apostrophe in a value is indistinguishable from a delimiter,
    and guessing rewrites the argument."""
    text = '{"a": "it\'s"}'
    assert single_to_double_quotes(text) == text
