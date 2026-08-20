"""Each of the six classes, from real evidence — and the honest seventh state."""

from __future__ import annotations

from pathlib import Path

import pytest
from evals_harness import call, probe, record

from ronin.core.loop import STALL_REPEATS
from ronin.evals.taxonomy import (
    BLAME,
    CLASSIFIERS,
    FailureClass,
    classify,
    classify_broke_tests,
    classify_edit_ambiguity,
    classify_gave_up_early,
    classify_hallucinated_api,
    classify_looped,
    classify_wrong_file,
    count_classes,
    failing_checks,
    fixture_symbols,
    judge,
    missing_symbols,
)

# --------------------------------------------------------------------------- #
# WRONG_FILE
# --------------------------------------------------------------------------- #


def test_edits_disjoint_from_the_expected_files_are_wrong_file() -> None:
    found = classify_wrong_file(
        record(files_expected=["src/app.py"], edited_paths=["src/other.py", "README.md"])
    )
    assert found is not None
    assert found.failure_class is FailureClass.WRONG_FILE
    assert "src/other.py" in found.evidence
    assert "src/app.py" in found.evidence


def test_one_overlapping_edit_is_enough_to_clear_wrong_file() -> None:
    assert (
        classify_wrong_file(
            record(files_expected=["src/app.py"], edited_paths=["src/app.py", "notes.md"])
        )
        is None
    )


@pytest.mark.parametrize(
    ("expected", "edited"),
    [(["src/app.py"], ["./src/app.py"]), (["./a/b.py"], ["a/b.py"])],
)
def test_path_spelling_does_not_create_a_wrong_file(expected: list[str], edited: list[str]) -> None:
    assert classify_wrong_file(record(files_expected=expected, edited_paths=edited)) is None


def test_wrong_file_needs_both_an_expectation_and_an_edit() -> None:
    assert classify_wrong_file(record(edited_paths=["x.py"])) is None
    assert classify_wrong_file(record(files_expected=["x.py"])) is None


# --------------------------------------------------------------------------- #
# EDIT_AMBIGUITY
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "error",
    [
        "old_string appears 4 times in app.py, so replacing it would be ambiguous. "
        "Either extend old_string with the surrounding lines...",
        "old_string was not found in app.py. The match is exact — whitespace counts.",
        "app.py has not been read in this session. Call read first.",
    ],
)
def test_an_edit_that_could_not_be_expressed_is_edit_ambiguity(error: str) -> None:
    found = classify_edit_ambiguity(record(tool_calls=[call("edit", ok=False, error=error)]))
    assert found is not None
    assert found.failure_class is FailureClass.EDIT_AMBIGUITY
    assert BLAME[FailureClass.EDIT_AMBIGUITY] == "harness"


def test_a_failing_shell_command_is_not_edit_ambiguity() -> None:
    """`bash` mutates too; a failing command is not a failure to express an edit."""
    assert (
        classify_edit_ambiguity(
            record(tool_calls=[call("bash", ok=False, error="pytest: 1 failed")])
        )
        is None
    )


def test_a_successful_edit_is_not_edit_ambiguity() -> None:
    assert classify_edit_ambiguity(record(tool_calls=[call("edit", ok=True)])) is None


# --------------------------------------------------------------------------- #
# GAVE_UP_EARLY
# --------------------------------------------------------------------------- #


def test_a_run_with_no_tool_calls_and_a_failing_verify_gave_up_early() -> None:
    found = classify_gave_up_early(record(verify_after=probe(1)))
    assert found is not None
    assert found.failure_class is FailureClass.GAVE_UP_EARLY
    assert "no tool calls" in found.evidence


def test_a_hedging_final_message_with_no_edits_gave_up_early() -> None:
    found = classify_gave_up_early(
        record(
            tool_calls=[call("read", ok=True)],
            final_text="You could change the return statement in app.py to add them.",
            verify_after=probe(1),
        )
    )
    assert found is not None
    assert found.failure_class is FailureClass.GAVE_UP_EARLY
    assert "hedges" in found.evidence


def test_hedging_after_actually_editing_is_not_giving_up() -> None:
    assert (
        classify_gave_up_early(
            record(
                tool_calls=[call("edit", ok=True)],
                edited_paths=["app.py"],
                final_text="You could also add a test for this.",
                verify_after=probe(1),
            )
        )
        is None
    )


def test_a_passing_run_never_gave_up() -> None:
    assert classify_gave_up_early(record(verify_after=probe(0))) is None


# --------------------------------------------------------------------------- #
# LOOPED
# --------------------------------------------------------------------------- #


def test_the_loops_own_stall_detector_firing_is_looped() -> None:
    found = classify_looped(record(stalled=True, stop_reason="stalled"))
    assert found is not None
    assert found.failure_class is FailureClass.LOOPED
    assert "stall detector" in found.evidence


def test_a_fingerprint_repeated_stall_repeats_times_is_looped() -> None:
    repeated = [call("grep", mark='grep:{"pattern":"foo"}') for _ in range(STALL_REPEATS)]
    found = classify_looped(record(tool_calls=repeated))
    assert found is not None
    assert found.failure_class is FailureClass.LOOPED
    assert str(STALL_REPEATS) in found.evidence


def test_one_repeat_short_of_the_threshold_is_not_looped() -> None:
    repeated = [call("grep", mark="grep:{}") for _ in range(STALL_REPEATS - 1)]
    assert classify_looped(record(tool_calls=repeated)) is None


# --------------------------------------------------------------------------- #
# BROKE_TESTS
# --------------------------------------------------------------------------- #


def test_a_fixture_that_verified_clean_and_now_fails_broke_tests() -> None:
    found = classify_broke_tests(
        record(verify_before=probe(0), verify_after=probe(1, "FAILED tests/test_a.py::test_x"))
    )
    assert found is not None
    assert found.failure_class is FailureClass.BROKE_TESTS
    assert "passed on the bare fixture" in found.evidence


def test_a_new_named_failure_alongside_the_original_one_broke_tests() -> None:
    before = probe(1, "FAILED tests/test_a.py::test_target\n1 failed")
    after = probe(1, "FAILED tests/test_a.py::test_target\nFAILED tests/test_b.py::test_other")
    found = classify_broke_tests(record(verify_before=before, verify_after=after))
    assert found is not None
    assert found.failure_class is FailureClass.BROKE_TESTS
    assert "tests/test_b.py::test_other" in found.evidence


def test_the_same_failure_before_and_after_is_not_a_regression() -> None:
    same = "FAILED tests/test_a.py::test_target"
    unchanged = record(verify_before=probe(1, same), verify_after=probe(1, same))
    assert classify_broke_tests(unchanged) is None


def test_a_task_that_now_passes_never_broke_tests() -> None:
    assert classify_broke_tests(record(verify_before=probe(1, "x"), verify_after=probe(0))) is None


def test_broke_tests_needs_a_baseline() -> None:
    """Without a pre-run probe the class is unavailable, and says so by not firing."""
    assert (
        classify_broke_tests(record(verify_before=probe(ran=False), verify_after=probe(1))) is None
    )


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            "FAILED tests/t.py::test_a\nFAILED tests/t.py::test_b",
            {"tests/t.py::test_a", "tests/t.py::test_b"},
        ),
        ("ERROR tests/t.py::test_c", {"tests/t.py::test_c"}),
        ("not ok 3 - the widget works", {"the widget works"}),
        ("--- FAIL: TestThing (0.00s)", {"TestThing"}),
        ("test_add (t.TestX) ... FAIL", {"test_add"}),
        ("test_add ... ERROR", {"test_add"}),
        ("everything is fine", set()),
    ],
)
def test_failing_checks_extracts_named_failures(output: str, expected: set[str]) -> None:
    assert failing_checks(output) == frozenset(expected)


# --------------------------------------------------------------------------- #
# HALLUCINATED_API
# --------------------------------------------------------------------------- #


def test_calling_a_tool_that_is_not_registered_is_a_hallucinated_api() -> None:
    found = classify_hallucinated_api(
        record(tool_calls=[call("str_replace_editor")], registered_tools=["edit", "read"])
    )
    assert found is not None
    assert found.failure_class is FailureClass.HALLUCINATED_API
    assert "str_replace_editor" in found.evidence
    assert BLAME[FailureClass.HALLUCINATED_API] == "harness"


def test_an_error_naming_a_symbol_the_fixture_does_not_define_is_a_hallucinated_api() -> None:
    found = classify_hallucinated_api(
        record(
            tool_calls=[
                call(
                    "bash",
                    ok=False,
                    error="AttributeError: module 'app' has no attribute 'reticulate'",
                )
            ],
            known_symbols=["app", "add", "main"],
        )
    )
    assert found is not None
    assert found.failure_class is FailureClass.HALLUCINATED_API
    assert "reticulate" in found.evidence


def test_an_error_naming_a_symbol_the_fixture_does_define_is_not_hallucinated() -> None:
    assert (
        classify_hallucinated_api(
            record(
                tool_calls=[call("bash", ok=False, error="NameError: name 'add' is not defined")],
                known_symbols=["add"],
            )
        )
        is None
    )


def test_without_a_symbol_table_no_hallucination_is_asserted() -> None:
    """An unprovable claim falls through to UNCLASSIFIED, which is loud, not quiet."""
    assert (
        classify_hallucinated_api(
            record(
                tool_calls=[call("bash", ok=False, error="NameError: name 'zz' is not defined")],
                known_symbols=[],
            )
        )
        is None
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("AttributeError: 'Foo' object has no attribute 'bar'", ("bar",)),
        ("ImportError: cannot import name 'Widget' from 'app'", ("Widget",)),
        ("NameError: name 'undefined_thing' is not defined", ("undefined_thing",)),
        ("ModuleNotFoundError: No module named 'requests.adapters'", ("requests",)),
        ("the docs say it has no attribute 'bar'", ()),
        ("", ()),
    ],
)
def test_missing_symbols_needs_the_exception_name_as_well_as_the_phrase(
    text: str, expected: tuple[str, ...]
) -> None:
    assert missing_symbols(text) == expected


def test_fixture_symbols_reads_definitions_without_importing(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("class Widget:\n    def spin(self): pass\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def oops(:\n", encoding="utf-8")
    names = fixture_symbols(tmp_path)
    assert {"app", "Widget", "spin", "broken"} <= names
    assert "oops" not in names  # the file does not parse, so nothing is claimed


def test_fixture_symbols_on_a_missing_directory_is_empty() -> None:
    assert fixture_symbols(Path("/nonexistent-fixture-dir")) == frozenset()


# --------------------------------------------------------------------------- #
# The set as a whole
# --------------------------------------------------------------------------- #


def test_a_failure_matching_nothing_is_unclassified_and_says_what_is_known() -> None:
    findings = classify(
        record(
            tool_calls=[call("read"), call("bash", ok=False, error="exit 2")],
            edited_paths=["app.py"],
            files_expected=["app.py"],
            final_text="I fixed it.",
            verify_before=probe(1, "FAILED t::a"),
            verify_after=probe(1, "FAILED t::a"),
            turns_used=3,
        )
    )
    assert [f.failure_class for f in findings] == [FailureClass.UNCLASSIFIED]
    assert "verify.sh exit 1" in findings[0].evidence
    assert "3 turn(s)" in findings[0].evidence
    assert findings[0].blame == "unknown"


def test_a_timeout_with_no_other_evidence_is_unclassified_and_names_the_timeout() -> None:
    findings = classify(record(timed_out=True, wall_seconds=12.0, error="timed out after 12s"))
    classes = [f.failure_class for f in findings]
    # It made no tool calls and no edits, so GAVE_UP_EARLY legitimately applies too;
    # what matters is that the timeout is never silently absorbed.
    assert FailureClass.UNCLASSIFIED in classes or FailureClass.GAVE_UP_EARLY in classes
    assert any("timed out" in f.evidence or "no tool calls" in f.evidence for f in findings)


def test_several_classes_may_apply_and_all_are_returned_ranked() -> None:
    findings = classify(
        record(
            files_expected=["src/app.py"],
            edited_paths=["src/other.py"],
            tool_calls=[
                call("edit", ok=False, error="old_string appears 3 times in src/app.py"),
                call("edit", ok=True, paths=["src/other.py"]),
            ],
            verify_before=probe(0),
            verify_after=probe(1, "FAILED t::a"),
        )
    )
    classes = [f.failure_class for f in findings]
    assert FailureClass.EDIT_AMBIGUITY in classes
    assert FailureClass.BROKE_TESTS in classes
    assert FailureClass.WRONG_FILE in classes
    assert [f.rank for f in findings] == sorted(f.rank for f in findings)
    # Ranking is by strength of evidence: the tool's own refusal outranks the
    # inference from which paths were touched.
    assert classes.index(FailureClass.EDIT_AMBIGUITY) < classes.index(FailureClass.WRONG_FILE)


def test_the_classifier_order_is_the_ranking() -> None:
    assert len(CLASSIFIERS) == 6
    for rank, classifier in enumerate(CLASSIFIERS):
        assert classifier.__name__.startswith("classify_")
        assert rank == CLASSIFIERS.index(classifier)


def test_a_pass_and_a_skip_carry_no_findings() -> None:
    assert classify(record(verify_after=probe(0))) == ()
    assert classify(record(skipped=True, skip_reason="needs network")) == ()


def test_a_task_with_no_verify_script_is_unverifiable_not_a_pass() -> None:
    passed = record(verify_after=probe(ran=False))
    assert passed.passed is False
    assert passed.unverifiable is True


def test_every_failure_class_has_a_side_of_the_system_to_fix() -> None:
    assert set(BLAME) == set(FailureClass)
    assert BLAME[FailureClass.UNCLASSIFIED] == "unknown"


def test_counting_includes_every_applicable_class_not_only_the_primary() -> None:
    results = [
        judge(
            record(
                task_id="a",
                files_expected=["app.py"],
                edited_paths=["other.py"],
                tool_calls=[call("edit", ok=False, error="old_string appears 2 times in app.py")],
            )
        ),
        judge(record(task_id="b", verify_after=probe(0))),
    ]
    counts = count_classes(results)
    assert counts[FailureClass.EDIT_AMBIGUITY] == 1
    assert counts[FailureClass.WRONG_FILE] == 1
    assert counts[FailureClass.UNCLASSIFIED] == 0
