"""The shim's remaining branches: mixed blocks, the coerce guard, and the final flush.

`test_shim.py` covers the parser thoroughly — 486 lines, including the partial-tag
guarantee at every chunk size and every split position. Three branches were left,
and each one is a decision about **what the model is told it asked for**, which is
the part of this module that is easy to get quietly wrong:

* a block holding several objects where only *some* are calls — the good ones must
  still run, and the bad ones must still be reported
* `_coerce_call`'s non-dict guard, the floor under "a call must be an object"
* prose still held in the parser's buffer when the stream ends, released by
  `finish()` — the branch that decides whether a trailing near-miss prefix reaches
  the user as text or vanishes

`MAX_REPAIRS` also had **zero references anywhere in the test tree**. The cap's
*behaviour* was tested with a literal 2, so changing the constant to 5 would have
left the suite green while silently tripling what a weak model costs.
"""

from __future__ import annotations

import json

import pytest
from test_shim import ScriptedModel, drain, text_then_done

from ronin.core.types import Message, Role, Text, ToolSpec
from ronin.providers.shim import (
    CLOSE_TAG,
    MAX_REPAIRS,
    OPEN_TAG,
    ShimClient,
    ShimStreamParser,
    _coerce_call,
    _read_payload,
)
from ronin.providers.types import FinishReason, ModelRequest, TextDelta

SPEC = ToolSpec(
    name="read_file",
    description="Read a file.",
    json_schema={"type": "object", "properties": {"path": {"type": "string"}}},
)


def request(*, system: str = "be terse") -> ModelRequest:
    return ModelRequest(
        model="local/qwen",
        messages=(Message(role=Role.USER, content_blocks=(Text("read a.py"),)),),
        system=system,
        tools=(SPEC,),
    )


# --------------------------------------------------------------------------- #
# A block holding several objects, only some of which are calls
# --------------------------------------------------------------------------- #


def test_the_good_calls_in_a_mixed_block_survive_and_the_bad_ones_are_reported() -> None:
    """Partial credit, deliberately.

    Dropping the whole block would tell a model that asked for two things that it
    asked for nothing — the module's stated reason for reading objects separately.
    Dropping only the *failure* would be worse: the model would never learn that
    half of what it emitted was unusable.
    """
    payload = '{"name": "read_file", "arguments": {"path": "a.py"}} {"not_a": "call"}'
    calls, failures = _read_payload(payload)
    assert [c.name for c in calls] == ["read_file"]
    assert len(failures) == 1
    assert "not a tool call object" in failures[0].error or "name" in failures[0].error
    assert '"not_a"' in failures[0].raw


def test_a_block_of_only_non_calls_is_all_failure_and_no_invented_call() -> None:
    calls, failures = _read_payload('{"a": 1} {"b": 2}')
    assert calls == ()
    assert failures, "a block with no call must report something"


def test_several_valid_calls_in_one_block_all_run() -> None:
    payload = (
        '{"name": "read_file", "arguments": {"path": "a.py"}} '
        '{"name": "read_file", "arguments": {"path": "b.py"}}'
    )
    calls, failures = _read_payload(payload)
    assert [json.loads(c.raw_arguments)["path"] for c in calls] == ["a.py", "b.py"]
    assert failures == ()


@pytest.mark.parametrize("obj", [None, 42, "a string", ["a", "list"], (1, 2)])
def test_a_call_that_is_not_an_object_is_refused(obj: object) -> None:
    """The floor under every path into `_coerce_call`.

    Reached defensively rather than through a payload today, which is exactly why
    it needs a test: a future caller that hands this a list would otherwise get an
    AttributeError instead of a clean refusal.
    """
    assert _coerce_call(obj) is None


# --------------------------------------------------------------------------- #
# The final flush
# --------------------------------------------------------------------------- #


async def test_prose_held_at_end_of_stream_reaches_the_user() -> None:
    """A stream ending mid-prefix, through the whole client.

    The parser holds `<ronin:to` back because it might become a tag. If `finish()`
    did not release it, the user would silently lose the tail of the answer — and
    the loss would be invisible, because nothing errors.
    """
    model = ScriptedModel([text_then_done("all done <ronin:to")])
    deltas, completed = await drain(ShimClient(model).stream(request()))
    visible = "".join(d.text for d in deltas if isinstance(d, TextDelta))
    assert visible.endswith("<ronin:to"), visible
    assert completed.finish is FinishReason.STOP


async def test_a_stream_that_ends_with_an_unterminated_block_does_not_leak_the_tag() -> None:
    """The other side of the same branch: a real open tag with no close is a
    failure, and its contents must never be shown as if they were the answer."""
    model = ScriptedModel(
        [
            text_then_done(f'thinking {OPEN_TAG}{{"name": "read_file"'),
            text_then_done("sorry, no tool"),
        ]
    )
    deltas, _ = await drain(ShimClient(model).stream(request()))
    visible = "".join(d.text for d in deltas if isinstance(d, TextDelta))
    assert OPEN_TAG not in visible
    assert "ronin:tool_call" not in visible
    assert '"name"' not in visible


# --------------------------------------------------------------------------- #
# The repair cap, bound to its constant
# --------------------------------------------------------------------------- #


def test_the_declared_repair_cap_is_two() -> None:
    """Bound by name so the number cannot drift away from the documented contract.

    Two is a deliberate figure: one retry recovers a model that simply mis-typed
    the format, and beyond that the failure is structural — a weak model that
    cannot emit the shape will not learn it on attempt four, it will just spend
    the user's tokens.
    """
    assert MAX_REPAIRS == 2


async def test_the_cap_is_the_constant_and_not_a_hardcoded_number() -> None:
    """Asserts the *observed* number of model calls equals `MAX_REPAIRS + 1`.

    If someone raises the constant, this test follows it; if someone changes the
    loop to ignore the constant, this test fails. That pairing is the point.
    """
    bad = f'{OPEN_TAG}{{"nope": 1}}{CLOSE_TAG}'
    model = ScriptedModel([text_then_done(bad) for _ in range(MAX_REPAIRS + 1)])
    await drain(ShimClient(model).stream(request()))
    assert len(model.requests) == MAX_REPAIRS + 1


async def test_a_recovered_call_stops_the_repair_loop_early() -> None:
    """The cap is a ceiling, not a quota — a model that fixes itself on the first
    retry must not be asked again."""
    good = f'{OPEN_TAG}{{"name": "read_file", "arguments": {{"path": "a.py"}}}}{CLOSE_TAG}'
    model = ScriptedModel(
        [text_then_done(f'{OPEN_TAG}{{"nope": 1}}{CLOSE_TAG}'), text_then_done(good)]
    )
    _, completed = await drain(ShimClient(model).stream(request()))
    assert len(model.requests) == 2
    assert completed.finish is FinishReason.TOOL_USE


# --------------------------------------------------------------------------- #
# The parser's guarantee, restated over adversarial prose
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "prose",
    [
        "compare a < b and c > d",
        "generics like Vec<String> are fine",
        "an xml sample: <ronin:tool_other>x</ronin:tool_other>",
        "a bare <ronin: colon",
        "<<<<ronin:tool_cal",
        "</ronin:tool_call> with no opener",
    ],
    ids=["lt-gt", "generics", "sibling-tag", "bare-prefix", "repeated-lt", "orphan-close"],
)
def test_adversarial_prose_is_released_verbatim_and_never_swallowed(prose: str) -> None:
    """Every one of these could plausibly be mistaken for a tag prefix.

    The failure mode being guarded is not a crash — it is text quietly disappearing
    into the parser's buffer, which reads to the user as the model saying less than
    it did.
    """
    parser = ShimStreamParser()
    visible = "".join(parser.feed(ch).visible for ch in prose)
    visible += parser.finish().visible
    assert visible == prose


# --------------------------------------------------------------------------- #
# A close tag inside a string argument
# --------------------------------------------------------------------------- #


def drive(chunks: list[str]) -> tuple[str, list[tuple[str, str]], list[str]]:
    """Feed chunks and return (visible, [(name, raw_arguments)], [errors])."""
    parser = ShimStreamParser()
    visible: list[str] = []
    calls: list[tuple[str, str]] = []
    failures: list[str] = []
    for chunk in chunks:
        emit = parser.feed(chunk)
        visible.append(emit.visible)
        calls.extend((c.name, c.raw_arguments) for c in emit.calls)
        failures.extend(f.error for f in emit.failures)
    emit = parser.finish()
    visible.append(emit.visible)
    calls.extend((c.name, c.raw_arguments) for c in emit.calls)
    failures.extend(f.error for f in emit.failures)
    return "".join(visible), calls, failures


@pytest.mark.parametrize("size", [1, 2, 3, 7, 19, 1000])
def test_a_close_tag_inside_a_string_argument_neither_leaks_nor_truncates(size: int) -> None:
    """The regression that made this fix necessary, at every chunk size.

    Before: the scan cut at the *first* close tag — the one inside the string — so
    the payload became `{…"content":"` which the truncation repair turned into
    `{"content": ""}`, and the leftover `"}}</ronin:tool_call>` was printed as
    prose. Asking the agent to write a file about its own tool protocol produced an
    empty file and a leaked tag, with no failure reported anywhere.
    """
    body = f'{{"name":"write_file","arguments":{{"content":"{CLOSE_TAG}"}}}}'
    stream = f"{OPEN_TAG}{body}{CLOSE_TAG}"
    chunks = [stream[i : i + size] for i in range(0, len(stream), size)]

    visible, calls, failures = drive(chunks)

    assert visible == "", f"leaked into visible text: {visible!r}"
    assert "ronin" not in visible
    assert failures == []
    assert len(calls) == 1
    assert json.loads(calls[0][1]) == {"content": CLOSE_TAG}, "the argument was corrupted"


def test_an_open_tag_inside_a_string_argument_is_also_preserved() -> None:
    """The other tag, which already worked — pinned so the fix cannot regress it."""
    body = f'{{"name":"w","arguments":{{"c":"{OPEN_TAG}"}}}}'
    visible, calls, _ = drive([f"{OPEN_TAG}{body}{CLOSE_TAG}"])
    assert visible == ""
    assert json.loads(calls[0][1]) == {"c": OPEN_TAG}


def test_an_escaped_quote_before_a_close_tag_does_not_end_the_string_early() -> None:
    r"""`"say \"hi\" </ronin:tool_call>"` — the escaped quotes must not make the
    scanner think the string closed, or the tag looks like structure again."""
    body = r'{"name":"w","arguments":{"c":"say \"hi\" </ronin:tool_call>"}}'
    visible, calls, _ = drive([f"{OPEN_TAG}{body}{CLOSE_TAG}"])
    assert visible == ""
    assert json.loads(calls[0][1])["c"] == 'say "hi" </ronin:tool_call>'


def test_two_calls_each_containing_a_close_tag_are_both_read() -> None:
    one = f'{OPEN_TAG}{{"name":"w","arguments":{{"c":"{CLOSE_TAG}"}}}}{CLOSE_TAG}'
    visible, calls, failures = drive([one + one])
    assert visible == ""
    assert len(calls) == 2
    assert failures == []


# --------------------------------------------------------------------------- #
# …without losing the truncation-repair path
# --------------------------------------------------------------------------- #


def test_a_payload_truncated_mid_string_is_still_recovered_at_end_of_stream() -> None:
    """The case the naive scan got *right*, which the fix must not break.

    Here the close tag sits inside an unterminated string too — but it is the real
    terminator, because the stream ends. Deciding that in `finish()` rather than in
    `feed()` is what lets both this and the literal-tag case work: while the stream
    is live the two are genuinely indistinguishable.
    """
    visible, calls, failures = drive([f'{OPEN_TAG}{{"name":"w","arguments":{{"c":"abc{CLOSE_TAG}'])
    assert visible == ""
    assert len(calls) == 1, failures
    assert json.loads(calls[0][1])["c"] == "abc"


def test_a_block_that_never_closes_at_all_is_a_failure_not_prose() -> None:
    visible, calls, failures = drive([f'{OPEN_TAG}{{"name":"w","arguments":{{"c":"abc'])
    assert visible == ""
    assert calls == []
    assert failures and "never emitted" in failures[0]


def test_text_after_a_recovered_truncated_block_is_not_leaked_as_prose() -> None:
    """The remainder is re-parsed rather than released blind, so a whole call
    hiding after the recovered terminator becomes a call and not visible text."""
    good = f'{OPEN_TAG}{{"name":"read_file","arguments":{{"path":"b.py"}}}}{CLOSE_TAG}'
    visible, calls, _ = drive([f'{OPEN_TAG}{{"name":"w","arguments":{{"c":"abc{CLOSE_TAG}{good}'])
    assert "ronin" not in visible
    assert [c[0] for c in calls] == ["w", "read_file"]


# --------------------------------------------------------------------------- #
# One call wrapped in chatter
# --------------------------------------------------------------------------- #


def test_a_single_call_buried_in_prose_is_read_like_two_calls_are() -> None:
    """The asymmetry that used to hit the commoner case.

    `len(objects) > 1` meant a block holding *two* objects amid chatter parsed
    while the same block holding *one* failed — and one is what a weak model
    actually emits when it prefixes an explanation.
    """
    calls, failures = _read_payload('Here you go: {"name":"read_file","arguments":{"path":"a"}}')
    assert [c.name for c in calls] == ["read_file"]
    assert failures == ()


def test_prose_with_no_object_at_all_is_still_a_failure() -> None:
    """The relaxation must not turn "the model said nothing usable" into a call."""
    calls, failures = _read_payload("I would rather not call a tool right now")
    assert calls == ()
    assert failures
