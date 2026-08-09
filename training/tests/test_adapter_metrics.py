"""The two first-class metrics, asserted against fakes with a *known* error rate.

Every test here builds a population whose answer is known by construction, so the
assertion is on the exact fraction rather than on "roughly right". A metric that is
only approximately tested is a metric that can be off by one and still look fine —
and this number is going into a published comparison.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from ronin_training.adapter.config import DIALECT_CLOSE_TAG, DIALECT_OPEN_TAG
from ronin_training.adapter.metrics import (
    NOT_MEASURED,
    VALID_CALL,
    CallCheck,
    MetricsError,
    RecoveryScore,
    Setback,
    SyntaxScore,
    TurnRecord,
    attempted_call,
    call_fingerprint,
    check_call,
    check_raw_call,
    find_call_blocks,
    load_tool_schemas,
    recovery_rate,
    tool_syntax_validity,
)

# A tiny registry: enough to exercise unknown-tool, wrong-type and missing-required
# without depending on the real 13-tool registry's exact shape.
SCHEMAS = {
    "read_file": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["path"],
    },
    "glob": {
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
        "required": ["pattern"],
    },
}


def _block(payload: str) -> str:
    return f"{DIALECT_OPEN_TAG}{payload}{DIALECT_CLOSE_TAG}"


def _call(name: str, arguments: object) -> str:
    return _block(json.dumps({"name": name, "arguments": arguments}))


# ------------------------------------------------------- tool-syntax validity


def _population(*, valid: int, invalid: int, silent: int) -> list[TurnRecord]:
    """A turn population whose validity is exactly ``valid / (valid + invalid)``."""
    turns: list[TurnRecord] = []
    for index in range(valid):
        turns.append(TurnRecord(task_id="t", call=VALID_CALL, fingerprint=f"ok:{index}"))
    for index in range(invalid):
        turns.append(
            TurnRecord(
                task_id="t",
                call=CallCheck(ok=False, reason=f"reason-{index}"),
                fingerprint=f"bad:{index}",
            )
        )
    turns.extend(TurnRecord(task_id="t") for _ in range(silent))
    return turns


@pytest.mark.parametrize(
    ("valid", "invalid", "silent", "expected"),
    [
        (10, 0, 0, 1.0),
        (0, 10, 0, 0.0),
        (7, 3, 0, 0.7),
        (7, 3, 25, 0.7),  # silent turns must not move the rate
        (1, 3, 0, 0.25),
        (3, 1, 12, 0.75),
    ],
)
def test_tool_syntax_validity_is_valid_over_attempted(
    valid: int, invalid: int, silent: int, expected: float
) -> None:
    score = tool_syntax_validity(_population(valid=valid, invalid=invalid, silent=silent))
    assert score.rate == pytest.approx(expected)
    assert score.attempts == valid + invalid
    assert score.valid == valid
    assert score.invalid == invalid
    assert score.turns == valid + invalid + silent
    assert score.no_attempt == silent


def test_a_model_that_never_calls_a_tool_does_not_score_one_hundred_percent() -> None:
    """The failure mode the denominator choice exists to prevent."""
    score = tool_syntax_validity(_population(valid=0, invalid=0, silent=40))
    assert score.rate is None
    assert score.turns == 40
    assert NOT_MEASURED in score.render()
    assert "0 of 40 turns attempted" in score.render()


def test_the_invalid_reasons_are_carried_verbatim_for_the_taxonomy() -> None:
    score = tool_syntax_validity(_population(valid=1, invalid=3, silent=0))
    assert score.reasons == ("reason-0", "reason-1", "reason-2")


def test_an_empty_run_measures_nothing_rather_than_zero() -> None:
    score = tool_syntax_validity([])
    assert score.rate is None
    assert score.render().startswith(NOT_MEASURED)


def test_an_impossible_syntax_score_is_rejected() -> None:
    with pytest.raises(MetricsError, match="impossible SyntaxScore"):
        SyntaxScore(turns=2, attempts=1, valid=2)
    with pytest.raises(MetricsError, match="impossible SyntaxScore"):
        SyntaxScore(turns=1, attempts=2, valid=0)


# -------------------------------------------------------------- recovery rate


def _setback(kind: Setback, *, repeat: bool, index: int) -> TurnRecord:
    failed = f"read_file:{{\"path\":\"missing-{index}\"}}"
    return TurnRecord(
        task_id="t",
        call=VALID_CALL,
        fingerprint=failed if repeat else f"glob:{{\"pattern\":\"*-{index}\"}}",
        setback=kind,
        setback_fingerprint=failed,
    )


@pytest.mark.parametrize("kind", list(Setback))
@pytest.mark.parametrize(
    ("adapted", "repeated", "expected"),
    [(4, 0, 1.0), (0, 4, 0.0), (3, 1, 0.75), (1, 3, 0.25), (5, 5, 0.5)],
)
def test_recovery_rate_counts_adaptation_over_setbacks(
    kind: Setback, adapted: int, repeated: int, expected: float
) -> None:
    turns = [_setback(kind, repeat=False, index=i) for i in range(adapted)]
    turns += [_setback(kind, repeat=True, index=100 + i) for i in range(repeated)]
    score = recovery_rate(turns)
    assert score.opportunities == adapted + repeated
    assert score.recovered == adapted
    assert score.repeated == repeated
    assert score.rate == pytest.approx(expected)
    assert score.for_setback(kind) == (adapted, adapted + repeated)


def test_the_approval_denial_case_is_counted_and_reported_separately() -> None:
    """A denial and a failed result are different failures and get different rows."""
    turns = [
        _setback(Setback.FAILED_RESULT, repeat=False, index=0),
        _setback(Setback.FAILED_RESULT, repeat=True, index=1),
        _setback(Setback.DENIAL, repeat=False, index=2),
        _setback(Setback.DENIAL, repeat=False, index=3),
        _setback(Setback.DENIAL, repeat=True, index=4),
    ]
    score = recovery_rate(turns)
    assert score.rate == pytest.approx(3 / 5)
    assert score.for_setback(Setback.FAILED_RESULT) == (1, 2)
    assert score.for_setback(Setback.DENIAL) == (2, 3)
    assert score.breakdown == (
        (Setback.DENIAL.value, 2, 3),
        (Setback.FAILED_RESULT.value, 1, 2),
    )


def test_answering_a_denial_with_no_tool_call_at_all_counts_as_recovery() -> None:
    """"I cannot do that; here is an alternative" is adaptation, not failure."""
    turns = [
        TurnRecord(
            task_id="t",
            setback=Setback.DENIAL,
            setback_fingerprint="run_command:{\"command\":\"rm -rf /\"}",
        )
    ]
    score = recovery_rate(turns)
    assert score.rate == 1.0
    assert score.recovered == 1


def test_turns_with_no_setback_are_not_recovery_opportunities() -> None:
    turns = [TurnRecord(task_id="t", call=VALID_CALL, fingerprint="x") for _ in range(9)]
    score = recovery_rate(turns)
    assert score.opportunities == 0
    assert score.rate is None
    assert NOT_MEASURED in score.render()
    assert "no failed tool result or denial" in score.render()


def test_a_turn_that_records_a_setback_must_name_the_failed_fingerprint() -> None:
    with pytest.raises(MetricsError, match="must name the fingerprint"):
        TurnRecord(task_id="t", setback=Setback.FAILED_RESULT)


def test_a_turn_that_attempted_a_call_must_carry_its_fingerprint() -> None:
    with pytest.raises(MetricsError, match="must carry its fingerprint"):
        TurnRecord(task_id="t", call=VALID_CALL)


def test_an_impossible_recovery_score_is_rejected() -> None:
    with pytest.raises(MetricsError, match="impossible RecoveryScore"):
        RecoveryScore(opportunities=1, recovered=2)


def test_a_turn_needs_a_task_id() -> None:
    with pytest.raises(MetricsError, match="task_id must be non-empty"):
        TurnRecord(task_id="")


def test_an_invalid_call_check_must_say_why() -> None:
    with pytest.raises(MetricsError, match="must say why"):
        CallCheck(ok=False)
    with pytest.raises(MetricsError, match="cannot carry a reason"):
        CallCheck(ok=True, reason="but why")


# --------------------------------------------------- turning raw output into a check


def test_a_well_formed_call_in_the_ronin_dialect_validates() -> None:
    text = f"I will look at it.\n{_call('read_file', {'path': 'a.py'})}"
    assert attempted_call(text)
    assert check_raw_call(text, schemas=SCHEMAS) == VALID_CALL


def test_the_v1_tool_call_tag_is_an_attempt_and_a_loudly_named_failure() -> None:
    """The dialect divergence must be a zero, not an unmeasured dash.

    ``packages/dialect`` (the v1 ronin-code-1.5b corpus) emits a bare ``<tool_call>``;
    ``src/ronin``'s format shim parses only ``<ronin:tool_call>``. A model trained on
    the wrong one makes calls the runtime never executes. If that counted as "no call
    attempted" it would drop out of the denominator and the whole run would report
    ``—`` instead of 0.000 — the difference between "we did not measure" and "it is
    completely broken".
    """
    text = '<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call>'
    assert attempted_call(text)
    check = check_raw_call(text, schemas=SCHEMAS)
    assert not check.ok
    assert "emitted the v1 <tool_call> dialect" in check.reason
    assert "data bug, not a training one" in check.reason
    score = tool_syntax_validity(
        [TurnRecord(task_id="t", call=check, fingerprint="read_file:{}")]
    )
    assert score.rate == 0.0
    assert score.attempts == 1


def test_the_two_dialect_tags_cannot_be_confused_by_substring() -> None:
    """``<ronin:tool_call>`` must not be seen as containing ``<tool_call>``."""
    from ronin_training.adapter.config import V1_DIALECT_OPEN_TAG

    assert V1_DIALECT_OPEN_TAG not in DIALECT_OPEN_TAG
    assert V1_DIALECT_OPEN_TAG not in DIALECT_CLOSE_TAG
    good = _call("read_file", {"path": "a.py"})
    assert check_raw_call(good, schemas=SCHEMAS).ok


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        (f"{DIALECT_OPEN_TAG}" + '{"name": "read_file"', "never emitted"),
        (_block("{not json}"), "not valid JSON"),
        (_block("[1, 2]"), "not an object"),
        (_block('{"arguments": {}}'), "no string 'name'"),
        (_block('{"name": "read_file"}'), "no 'arguments' key"),
        (_call("nope", {}), "unknown tool"),
        (_call("read_file", '{"path": "a"}'), "JSON string, not an object"),
        (_call("read_file", 7), "not an object"),
        (_call("read_file", {}), "violates its schema"),
        (_call("read_file", {"path": 7}), "violates its schema"),
    ],
)
def test_every_syntax_failure_mode_gets_its_own_named_reason(
    text: str, fragment: str
) -> None:
    check = check_raw_call(text, schemas=SCHEMAS)
    assert not check.ok
    assert fragment in check.reason, check.reason


def test_scoring_a_silent_turn_as_a_bad_call_is_refused() -> None:
    """Guards the denominator: silence is not an invalid call."""
    with pytest.raises(MetricsError, match="no tool-call attempt"):
        check_raw_call("just prose", schemas=SCHEMAS)


def test_find_call_blocks_returns_every_payload_in_order() -> None:
    text = _call("read_file", {"path": "a"}) + "\n" + _call("glob", {"pattern": "*"})
    blocks = find_call_blocks(text)
    assert len(blocks) == 2
    assert json.loads(blocks[0])["name"] == "read_file"
    assert json.loads(blocks[1])["name"] == "glob"


def test_check_call_accepts_a_valid_object_and_rejects_an_unknown_tool() -> None:
    assert check_call("glob", {"pattern": "*.py"}, schemas=SCHEMAS).ok
    bad = check_call("write_everything", {}, schemas=SCHEMAS)
    assert not bad.ok and "unknown tool" in bad.reason


def test_the_real_registry_loads_and_covers_ronins_tools() -> None:
    schemas = load_tool_schemas()
    assert "read" in schemas
    assert "bash" in schemas
    assert check_call("read", {"path": "pyproject.toml"}, schemas=schemas).ok
    # And the v1 name is now *invalid*, which is the guard that matters: a metric that
    # still accepted `read_file` would score a v1-dialect adapter as perfect.
    stale = check_call("read_file", {"path": "pyproject.toml"}, schemas=schemas)
    assert not stale.ok and "unknown tool" in stale.reason


def test_a_missing_registry_file_names_itself(tmp_path: Path) -> None:
    with pytest.raises(MetricsError, match="cannot read the tool registry"):
        load_tool_schemas(str(tmp_path / "nope.json"))


# ------------------------------------------------------------------- fingerprints


def test_the_fingerprint_is_the_runtimes_own_definition() -> None:
    """Not a reimplementation: the metric and the stall detector must agree."""
    from ronin.core.loop import fingerprint
    from ronin.core.types import ToolUse

    mine = call_fingerprint("read_file", {"b": 2, "a": 1})
    theirs = fingerprint(ToolUse(id="x", name="read_file", arguments={"a": 1, "b": 2}))
    assert mine == theirs


def test_the_fingerprint_ignores_argument_order() -> None:
    assert call_fingerprint("glob", {"a": 1, "b": 2}) == call_fingerprint(
        "glob", {"b": 2, "a": 1}
    )
    assert call_fingerprint("glob", {"a": 1}) != call_fingerprint("glob", {"a": 2})


def test_recovery_uses_the_real_fingerprint_end_to_end() -> None:
    """The one wiring test: real fingerprints, so a repeat is detected as one."""
    failed = call_fingerprint("read_file", {"path": "missing.py"})
    repeat = TurnRecord(
        task_id="t",
        call=VALID_CALL,
        fingerprint=call_fingerprint("read_file", {"path": "missing.py"}),
        setback=Setback.FAILED_RESULT,
        setback_fingerprint=failed,
    )
    adapt = TurnRecord(
        task_id="t",
        call=VALID_CALL,
        fingerprint=call_fingerprint("glob", {"pattern": "**/*.py"}),
        setback=Setback.FAILED_RESULT,
        setback_fingerprint=failed,
    )
    assert repeat.repeated_setback is True
    assert adapt.repeated_setback is False
    assert recovery_rate([repeat, adapt]).rate == 0.5
