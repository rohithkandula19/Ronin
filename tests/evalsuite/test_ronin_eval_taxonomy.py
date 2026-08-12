"""RE.1 acceptance: the failure taxonomy answers "fix the harness or the model".

This is the spec-level acceptance test for the failure taxonomy, and it is deliberately
distinct from ``test_ronin_evals_taxonomy.py`` — that file unit-tests each classifier's
edges; this one pins the *contract the harvest and scoreboard depend on*: every failure
kind is reachable from a crafted run, a pass carries no finding, the counts tally a mix,
and every kind names a side of the system to fix.

A note on names, because the RE.1 spec and the shipped module chose different ones. The
spec sketches ``FailureKind`` with ``NONE``/``UNKNOWN`` and ``classify -> FailureKind``;
the module that shipped (:mod:`ronin.evals.taxonomy`) is richer and is what actually
wires into the report, so this test targets it rather than a second, weaker copy:

* ``FailureKind``        -> :class:`ronin.evals.taxonomy.FailureClass`
* ``NONE`` (passed)      -> ``classify`` returns ``()`` (no finding for a pass)
* ``UNKNOWN``            -> :attr:`FailureClass.UNCLASSIFIED`
* ``classify -> kind``   -> ``classify -> tuple[Finding, ...]`` ranked, primary first
* ``taxonomy_counts``    -> :func:`ronin.evals.taxonomy.count_classes`
"""

from __future__ import annotations

from evals_harness import call, probe, record

from ronin.evals.taxonomy import (
    BLAME,
    FailureClass,
    RunRecord,
    classify,
    count_classes,
    judge,
)

# --------------------------------------------------------------------------- #
# One crafted run per kind -> classify returns that kind as the primary finding
# --------------------------------------------------------------------------- #


def _looped() -> RunRecord:
    # A tool call present so this is *purely* looped — with none, GAVE_UP_EARLY (no tool
    # calls) legitimately co-fires, which is a different test's subject.
    return record(
        stalled=True,
        stop_reason="stalled",
        tool_calls=[call("grep", ok=True)],
        verify_after=probe(1),
    )


def _hallucinated_api() -> RunRecord:
    return record(
        tool_calls=[call("str_replace_editor")],
        registered_tools=["edit", "read"],
        verify_after=probe(1),
    )


def _edit_ambiguity() -> RunRecord:
    return record(
        tool_calls=[call("edit", ok=False, error="old_string appears 3 times in app.py")],
        verify_after=probe(1),
    )


def _broke_tests() -> RunRecord:
    return record(
        tool_calls=[call("edit", ok=True, paths=["app.py"])],
        edited_paths=["app.py"],
        verify_before=probe(0),
        verify_after=probe(1, "FAILED tests/test_app.py::test_add"),
    )


def _wrong_file() -> RunRecord:
    return record(
        files_expected=["src/app.py"],
        edited_paths=["src/other.py"],
        tool_calls=[call("edit", ok=True, paths=["src/other.py"])],
        verify_after=probe(1),
    )


def _gave_up_early() -> RunRecord:
    return record(tool_calls=[], edited_paths=[], verify_after=probe(1))


#: One builder per FailureClass that names a real failure (UNCLASSIFIED is the absence of
#: a match and is exercised separately, in the shipped unit test).
BY_KIND: dict[FailureClass, RunRecord] = {
    FailureClass.LOOPED: _looped(),
    FailureClass.HALLUCINATED_API: _hallucinated_api(),
    FailureClass.EDIT_AMBIGUITY: _edit_ambiguity(),
    FailureClass.BROKE_TESTS: _broke_tests(),
    FailureClass.WRONG_FILE: _wrong_file(),
    FailureClass.GAVE_UP_EARLY: _gave_up_early(),
}


def test_each_failure_kind_is_reachable_as_the_primary_finding() -> None:
    """The spec's headline: one crafted run per kind, and classify names that kind first."""
    for kind, run in BY_KIND.items():
        findings = classify(run)
        assert findings, f"{kind} produced no finding"
        assert findings[0].failure_class is kind, (
            f"expected {kind} primary, got {[f.failure_class for f in findings]}"
        )


def test_the_kinds_reached_cover_every_non_unclassified_class() -> None:
    """No failure class is unreachable — a bucket nothing lands in is dead taxonomy."""
    reached = {classify(run)[0].failure_class for run in BY_KIND.values()}
    assert reached == set(FailureClass) - {FailureClass.UNCLASSIFIED}


# --------------------------------------------------------------------------- #
# A passing run is the spec's NONE: no finding at all
# --------------------------------------------------------------------------- #


def test_a_passing_run_carries_no_finding() -> None:
    """The spec's NONE. A green verify.sh is not a failure, so there is nothing to class."""
    assert classify(record(verify_after=probe(0))) == ()


def test_a_skip_carries_no_finding_either() -> None:
    assert classify(record(skipped=True, skip_reason="needs network")) == ()


# --------------------------------------------------------------------------- #
# count_classes (the spec's taxonomy_counts) tallies a mix
# --------------------------------------------------------------------------- #


def test_count_classes_tallies_a_mix_and_leaves_the_pass_uncounted() -> None:
    results = [
        judge(_looped()),
        judge(_edit_ambiguity()),
        judge(_wrong_file()),
        judge(record(task_id="green", verify_after=probe(0))),  # a pass: contributes nothing
    ]
    counts = count_classes(results)
    assert counts[FailureClass.LOOPED] == 1
    assert counts[FailureClass.EDIT_AMBIGUITY] == 1
    assert counts[FailureClass.WRONG_FILE] == 1
    assert counts[FailureClass.HALLUCINATED_API] == 0
    # The pass added no finding, so the denominator is failures and nothing else.
    assert sum(counts.values()) == 3


def test_a_run_with_several_kinds_counts_each_of_them() -> None:
    """A failure can be more than one thing, and the counts must not collapse it to one."""
    multi = record(
        files_expected=["src/app.py"],
        edited_paths=["src/other.py"],
        tool_calls=[
            call("edit", ok=False, error="old_string appears 3 times in src/app.py"),
            call("edit", ok=True, paths=["src/other.py"]),
        ],
        verify_before=probe(0),
        verify_after=probe(1, "FAILED t::a"),
    )
    classes = {finding.failure_class for finding in classify(multi)}
    assert {
        FailureClass.EDIT_AMBIGUITY,
        FailureClass.BROKE_TESTS,
        FailureClass.WRONG_FILE,
    } <= classes


# --------------------------------------------------------------------------- #
# The taxonomy's purpose: every kind names a side of the system to fix
# --------------------------------------------------------------------------- #


def test_every_kind_points_at_the_harness_or_the_model() -> None:
    """The one output anyone acts on: blame is total over the classes, and it is either
    the harness, the model, or an honest 'unknown' for the unclassified."""
    assert set(BLAME) == set(FailureClass)
    assert set(BLAME.values()) == {"harness", "model", "unknown"}
    assert BLAME[FailureClass.EDIT_AMBIGUITY] == "harness"
    assert BLAME[FailureClass.HALLUCINATED_API] == "harness"
    assert BLAME[FailureClass.WRONG_FILE] == "model"
    assert BLAME[FailureClass.UNCLASSIFIED] == "unknown"
