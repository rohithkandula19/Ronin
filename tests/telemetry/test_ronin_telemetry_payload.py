"""The payload: is the forbidden thing unrepresentable, and are the buckets stable?

The load-bearing test in this file is
:func:`test_the_serialized_keys_are_exactly_the_allowlist`. Everything else here is
about the boundaries. That one is about the *shape*: it fails the moment someone adds a
field to :class:`~ronin.telemetry.TaskOutcome`, which is the event that must never be
silent in the most privacy-sensitive module in the repository.
"""
from __future__ import annotations

import dataclasses
import json

import pytest
from telemetry_harness import FIXED_ENVIRONMENT, payload

from ronin.telemetry import (
    DURATION_BOUNDS,
    PAYLOAD_FIELDS,
    TURN_BOUNDS,
    UNKNOWN_VERSION,
    DurationBucket,
    OsName,
    Outcome,
    TaskCategory,
    TaskOutcome,
    TaxonomyClass,
    TurnBucket,
    describe_environment,
    duration_bucket,
    normalize_ronin_version,
    os_name_for,
    outcome_for_exit_code,
    outcome_payload,
    serialize,
    to_json,
    turn_bucket,
)

# --------------------------------------------------------------------------- #
# the allowlist
# --------------------------------------------------------------------------- #


def test_the_serialized_keys_are_exactly_the_allowlist() -> None:
    body = json.loads(to_json(payload()))
    assert tuple(sorted(body)) == PAYLOAD_FIELDS
    # Sorted output, so the tuple's own order is the wire order and a diff of two
    # payloads is readable.
    assert tuple(body) == PAYLOAD_FIELDS


def test_every_dataclass_field_appears_in_the_serialized_payload() -> None:
    """A field added to the dataclass but not to :func:`serialize` is a silent drop."""
    declared = {field.name for field in dataclasses.fields(TaskOutcome)}
    assert declared == set(PAYLOAD_FIELDS)


def test_every_serialized_value_is_a_string_or_a_bool() -> None:
    """No ints, no floats, no containers — a container is where free text gets in."""
    for value in serialize(payload()).values():
        assert isinstance(value, str | bool), value


def test_the_payload_has_no_field_that_could_hold_free_text() -> None:
    """The structural claim, asserted rather than trusted.

    Every field is an enum, a bool, or one of the two version strings that
    ``__post_init__`` pattern-checks. If this test fails, someone added a plain ``str``
    and a prompt can now be smuggled onto the wire.
    """
    pattern_checked = {"ronin_version", "python_version"}
    for field in dataclasses.fields(TaskOutcome):
        if field.name in pattern_checked:
            continue
        assert field.type in ("bool", "OsName", "TaskCategory", "Outcome",
                              "TaxonomyClass", "TurnBucket", "DurationBucket"), field


def test_the_payload_is_frozen_so_a_caller_cannot_widen_it_after_construction() -> None:
    built = payload()
    with pytest.raises(dataclasses.FrozenInstanceError):
        built.outcome = Outcome.ERROR  # type: ignore[misc]


def test_json_is_sorted_and_single_line_so_two_runs_diff_cleanly() -> None:
    line = to_json(payload())
    assert "\n" not in line
    assert json.loads(line) == serialize(payload())


# --------------------------------------------------------------------------- #
# version validation — the only strings on the payload
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "version",
    [
        "1.0.0+dirty",
        "1.0.0-acme-internal",
        "/Users/someone/src/secret-project",
        "fix the auth timeout for acme corp",
        "",
        "(not installed; running from a source tree)",
    ],
)
def test_a_ronin_version_that_is_not_dotted_digits_is_refused(version: str) -> None:
    with pytest.raises(ValueError, match="dotted digits"):
        dataclasses.replace(payload(), ronin_version=version)


@pytest.mark.parametrize("version", ["1", "1.0", "1.0.0", "1.0.0.1", UNKNOWN_VERSION])
def test_an_acceptable_ronin_version_is_accepted(version: str) -> None:
    assert dataclasses.replace(payload(), ronin_version=version).ronin_version == version


@pytest.mark.parametrize("raw", ["3.11.7", "3", "", "3.11rc1", "three.eleven"])
def test_a_python_version_that_is_not_major_minor_is_refused(raw: str) -> None:
    with pytest.raises(ValueError, match=r"major\.minor"):
        dataclasses.replace(payload(), python_version=raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.0.0", "1.0.0"),
        ("2", "2"),
        ("1.0.0+dirty.git.acme-internal", UNKNOWN_VERSION),
        ("(not installed; running from a source tree)", UNKNOWN_VERSION),
        ("", UNKNOWN_VERSION),
    ],
)
def test_a_local_version_is_normalized_rather_than_carried_onto_the_wire(
    raw: str, expected: str
) -> None:
    assert normalize_ronin_version(raw) == expected


def test_describe_environment_normalizes_so_a_dev_checkout_leaks_no_branch_name() -> None:
    described = describe_environment(
        ronin_version="1.0.0+feature.acme-sso", python_version="3.12", platform="darwin"
    )
    assert described.ronin_version == UNKNOWN_VERSION
    assert described.os_name is OsName.MACOS


# --------------------------------------------------------------------------- #
# os classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("linux", OsName.LINUX),
        ("linux2", OsName.LINUX),
        ("darwin", OsName.MACOS),
        ("win32", OsName.WINDOWS),
        ("cygwin", OsName.WINDOWS),
        ("msys", OsName.WINDOWS),
        ("freebsd13", OsName.OTHER),
        ("", OsName.OTHER),
    ],
)
def test_platform_strings_map_to_the_four_families(platform: str, expected: OsName) -> None:
    assert os_name_for(platform) is expected


# --------------------------------------------------------------------------- #
# bucket boundaries — the exact edges, because an off-by-one here is invisible
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("turns", "expected"),
    [
        (1, TurnBucket.ONE),
        (2, TurnBucket.TWO_TO_THREE),
        (3, TurnBucket.TWO_TO_THREE),
        (4, TurnBucket.FOUR_TO_SEVEN),
        (7, TurnBucket.FOUR_TO_SEVEN),
        (8, TurnBucket.EIGHT_TO_FIFTEEN),
        (15, TurnBucket.EIGHT_TO_FIFTEEN),
        (16, TurnBucket.SIXTEEN_PLUS),
        (10_000, TurnBucket.SIXTEEN_PLUS),
    ],
)
def test_turn_buckets_are_half_open_at_the_documented_edges(
    turns: int, expected: TurnBucket
) -> None:
    assert turn_bucket(turns) is expected


@pytest.mark.parametrize("turns", [0, -1])
def test_a_turn_count_below_one_is_a_harness_bug_and_raises(turns: int) -> None:
    with pytest.raises(ValueError, match="turns must be >= 1"):
        turn_bucket(turns)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, DurationBucket.UNDER_1S),
        (0.999, DurationBucket.UNDER_1S),
        (1.0, DurationBucket.ONE_TO_5S),
        (4.999, DurationBucket.ONE_TO_5S),
        (5.0, DurationBucket.FIVE_TO_15S),
        (14.999, DurationBucket.FIVE_TO_15S),
        (15.0, DurationBucket.FIFTEEN_TO_60S),
        (59.999, DurationBucket.FIFTEEN_TO_60S),
        (60.0, DurationBucket.ONE_TO_5M),
        (299.999, DurationBucket.ONE_TO_5M),
        (300.0, DurationBucket.OVER_5M),
        (86_400.0, DurationBucket.OVER_5M),
    ],
)
def test_duration_buckets_are_half_open_at_the_documented_edges(
    seconds: float, expected: DurationBucket
) -> None:
    assert duration_bucket(seconds) is expected


def test_a_negative_duration_raises_rather_than_clamping() -> None:
    """Clamping would hide a subtract-in-the-wrong-order bug in the least-read path."""
    with pytest.raises(ValueError, match="seconds must be >= 0"):
        duration_bucket(-0.001)


def test_every_bucket_member_is_reachable_from_the_boundary_table() -> None:
    """A bucket no boundary produces is a value that would never appear in the data."""
    from_turns = {turn_bucket(n) for n in range(1, 40)}
    assert from_turns == set(TurnBucket)
    probes = [0.0, 1.0, 5.0, 15.0, 60.0, 300.0]
    assert {duration_bucket(value) for value in probes} == set(DurationBucket)


def test_the_boundary_tables_are_ascending_and_agree_with_their_enums() -> None:
    turn_uppers = [upper for upper, _ in TURN_BOUNDS]
    assert turn_uppers == sorted(turn_uppers)
    duration_uppers = [upper for upper, _ in DURATION_BOUNDS]
    assert duration_uppers == sorted(duration_uppers)
    # One bound per bucket except the open-ended last one.
    assert len(TURN_BOUNDS) == len(TurnBucket) - 1
    assert len(DURATION_BOUNDS) == len(DurationBucket) - 1


# --------------------------------------------------------------------------- #
# assembling a payload from a turn's raw facts
# --------------------------------------------------------------------------- #


def test_outcome_payload_buckets_and_loses_the_exact_values() -> None:
    built = outcome_payload(
        FIXED_ENVIRONMENT,
        task_category=TaskCategory.REFACTOR,
        outcome=Outcome.DONE,
        turns=9,
        seconds=1_247.318,
        taxonomy_class=TaxonomyClass.NONE,
        gate_denied=True,
    )
    assert built.turn_bucket is TurnBucket.EIGHT_TO_FIFTEEN
    assert built.duration_bucket is DurationBucket.OVER_5M
    body = to_json(built)
    # The exact figures the caller passed cannot be recovered from the wire form.
    assert "9" not in body.replace('"8-15"', "")
    assert "1247" not in body
    assert "318" not in body


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, Outcome.DONE),
        (1, Outcome.ERROR),
        (2, Outcome.NEEDS_APPROVAL),
        (3, Outcome.ERROR),
        (-1, Outcome.ERROR),
        (127, Outcome.ERROR),
    ],
)
def test_exit_codes_map_onto_outcomes_and_an_unknown_code_is_not_a_success(
    code: int, expected: Outcome
) -> None:
    assert outcome_for_exit_code(code) is expected


def test_every_enum_value_survives_a_round_trip_through_json() -> None:
    """Table-driven over the whole vocabulary: a member whose value is not JSON-safe
    would only fail on the one payload that happened to carry it."""
    for category in TaskCategory:
        for outcome in Outcome:
            for taxonomy in TaxonomyClass:
                built = payload(
                    task_category=category, outcome=outcome, taxonomy_class=taxonomy
                )
                body = json.loads(to_json(built))
                assert body["task_category"] == category.value
                assert body["outcome"] == outcome.value
                assert body["taxonomy_class"] == taxonomy.value
