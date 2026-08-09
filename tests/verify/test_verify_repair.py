"""The repair loop, and the noise stripping the whole thing rests on."""
from __future__ import annotations

import pytest
from verify_harness import (
    MYPY_TWO_ERRORS,
    MYPY_TWO_ERRORS_SHIFTED,
    PYTEST_ONE_FAILURE,
    PYTEST_ONE_FAILURE_NOISY,
    PYTEST_OTHER_FAILURE,
    PYTEST_TWO_FAILURES,
    RUFF_ONE_ERROR,
)

from ronin.verify.repair import (
    FailureKind,
    FailureSnapshot,
    RepairVerdict,
    failure_count,
    failure_signature,
    normalize_line,
    parse_failures,
    repair_loop,
)
from ronin.verify.runner import VerifyOutcome, VerifyPlan

# --------------------------------------------------------------------------- #
# Normalization — the crux
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("tests/a.py:14: in test_x", "tests/a.py:L: in test_x"),
        ("src/a.py:14:7: F401 unused", "src/a.py:L: F401 unused"),
        ("app/b.ts:3:9: error TS2322", "app/b.ts:L: error TS2322"),
        ("rootdir: /tmp/pytest-of-alice/pytest-17/proj0", "rootdir: <tmp>"),
        ("cache at /var/folders/xy/abc123/T/pytest-9", "cache at <tmp>"),
        ("<Widget object at 0x7f3c9a1b2d30>", "<Widget object at 0xADDR>"),
        ("run id 3f2504e0-4f89-11d3-9a0c-0305e82c3301", "run id <uuid>"),
        ("1 failed, 2 passed in 0.11s", "1 failed, 2 passed in <dur>"),
        ("took 1.5 seconds", "took <dur>"),
        ('File "x.py", line 233, in widget', 'File "x.py", line L, in widget'),
        ("[gw3] linux -- Python", "[gw] linux -- Python"),
        ("E    AssertionError:  assert 1 == 2", "E AssertionError: assert 1 == 2"),
    ],
)
def test_every_class_of_run_to_run_noise_is_stripped(line: str, expected: str) -> None:
    assert normalize_line(line) == expected


def test_the_same_pytest_failure_has_one_signature_across_noisy_runs() -> None:
    """The assertion the repair cap depends on.

    The two samples differ in tmpdir, line number, duration, plugin list and test
    count — every difference a real second run produces — and in nothing else. A
    signature that separates them would let the loop report "making progress"
    forever.
    """
    assert failure_signature(PYTEST_ONE_FAILURE) == failure_signature(PYTEST_ONE_FAILURE_NOISY)


def test_the_same_mypy_errors_have_one_signature_after_lines_shift() -> None:
    assert failure_signature(MYPY_TWO_ERRORS) == failure_signature(MYPY_TWO_ERRORS_SHIFTED)


def test_a_genuinely_different_failure_has_a_different_signature() -> None:
    assert failure_signature(PYTEST_ONE_FAILURE) != failure_signature(PYTEST_OTHER_FAILURE)
    assert failure_signature(PYTEST_ONE_FAILURE) != failure_signature(PYTEST_TWO_FAILURES)


def test_reordered_failures_have_the_same_signature() -> None:
    """A shuffling test runner must not look like a changed failure."""
    lines = PYTEST_TWO_FAILURES.splitlines()
    first, second = (index for index, line in enumerate(lines) if line.startswith("FAILED"))
    lines[first], lines[second] = lines[second], lines[first]

    assert failure_signature("\n".join(lines)) == failure_signature(PYTEST_TWO_FAILURES)


def test_no_failures_hashes_to_a_named_constant_not_an_empty_string() -> None:
    assert failure_signature("") == "none"
    assert failure_signature("== 4 passed in 0.02s ==") == "none"


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_a_failing_test_is_recorded_once_with_its_assertion() -> None:
    failures = parse_failures(PYTEST_ONE_FAILURE)
    assert len(failures) == 1
    assert failures[0].kind is FailureKind.TEST
    assert failures[0].where == "tests/pkg/test_thing.py::test_widget_rounds"
    assert failures[0].detail == "AssertionError: assert 1.0 == 1.01"


def test_type_errors_in_one_file_stay_separate_failures() -> None:
    failures = parse_failures(MYPY_TWO_ERRORS)
    assert len(failures) == 2
    assert {failure.kind for failure in failures} == {FailureKind.TYPE}
    assert all(failure.where == "src/thing.py" for failure in failures)


def test_a_lint_error_carries_its_rule_code() -> None:
    failures = parse_failures(RUFF_ONE_ERROR)
    assert failures[0].kind is FailureKind.LINT
    assert failures[0].detail.startswith("F401")


def test_unrecognized_output_still_gets_a_stable_identity() -> None:
    text = "make: *** No rule to make target 'test'. Stop."
    failures = parse_failures(text)
    assert len(failures) == 1
    assert failures[0].kind is FailureKind.OTHER
    assert failure_signature(text) == failure_signature(text + "\n")


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (PYTEST_ONE_FAILURE, 1),
        (PYTEST_TWO_FAILURES, 2),
        (MYPY_TWO_ERRORS, 2),
        (RUFF_ONE_ERROR, 1),
        ("", 0),
    ],
)
def test_the_runners_own_count_is_preferred(output: str, expected: int) -> None:
    assert failure_count(output) == expected


def test_an_unknown_verify_outcome_is_not_a_pass() -> None:
    snapshot = FailureSnapshot.from_outcome(VerifyOutcome(plan=VerifyPlan()))
    assert snapshot.ok is False


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def _snapshot(output: str, *, ok: bool = False) -> FailureSnapshot:
    return FailureSnapshot.from_output(output, ok=ok)


class _Script:
    """A scripted verify/fix pair: one output per verify call, diffs recorded."""

    def __init__(self, outputs: list[str | None]) -> None:
        self.outputs = outputs
        self.verify_calls = 0
        self.fix_calls: list[int] = []

    async def verify(self) -> FailureSnapshot:
        index = min(self.verify_calls, len(self.outputs) - 1)
        self.verify_calls += 1
        output = self.outputs[index]
        if output is None:
            return _snapshot("", ok=True)
        return _snapshot(output)

    async def fix(self, snapshot: FailureSnapshot, attempt: int) -> str:
        del snapshot
        self.fix_calls.append(attempt)
        return f"diff for attempt {attempt}"


async def test_a_green_baseline_needs_no_attempts() -> None:
    script = _Script([None])
    report = await repair_loop(verify=script.verify, fix=script.fix)
    assert report.verdict is RepairVerdict.ALREADY_GREEN
    assert report.attempts == ()
    assert script.fix_calls == []
    assert "already green" in report.render()


async def test_a_successful_repair_stops_immediately() -> None:
    script = _Script([PYTEST_ONE_FAILURE, PYTEST_OTHER_FAILURE, None])
    report = await repair_loop(verify=script.verify, fix=script.fix)
    assert report.verdict is RepairVerdict.FIXED
    assert report.fixed
    assert len(report.attempts) == 2
    assert "fixed after 2 attempt(s)" in report.render()


async def test_three_identical_signatures_stop_the_loop_and_report_honestly() -> None:
    """The stop rule, stated in observations: the failing run plus two dead edits."""
    script = _Script([PYTEST_ONE_FAILURE, PYTEST_ONE_FAILURE_NOISY, PYTEST_ONE_FAILURE])

    report = await repair_loop(verify=script.verify, fix=script.fix)

    assert report.verdict is RepairVerdict.STUCK
    assert report.fixed is False
    assert report.identical_seen == 3
    assert report.stable_signature == failure_signature(PYTEST_ONE_FAILURE)
    rendered = report.render()
    assert rendered.startswith("i could not fix this.")
    assert "what i tried" in rendered
    assert "diff for attempt 1" in rendered
    assert "what i think is wrong" in rendered
    assert report.stable_signature in rendered


async def test_a_changing_failure_uses_the_whole_cap_then_reports_exhausted() -> None:
    script = _Script(
        [PYTEST_TWO_FAILURES, PYTEST_OTHER_FAILURE, RUFF_ONE_ERROR, MYPY_TWO_ERRORS]
    )

    report = await repair_loop(verify=script.verify, fix=script.fix)

    assert report.verdict is RepairVerdict.EXHAUSTED
    assert len(report.attempts) == 3
    assert "never cleared inside the 3-attempt cap" in report.diagnosis


async def test_a_rising_failure_count_is_reported_as_a_regression() -> None:
    script = _Script(
        [PYTEST_ONE_FAILURE, PYTEST_OTHER_FAILURE, RUFF_ONE_ERROR, PYTEST_TWO_FAILURES]
    )

    report = await repair_loop(verify=script.verify, fix=script.fix)

    assert report.verdict is RepairVerdict.REGRESSED
    assert "i made it worse: 1 failing before, 2 after" in report.diagnosis
    assert "reverted" in report.render()


async def test_progress_is_named_per_attempt_in_both_directions() -> None:
    script = _Script(
        [PYTEST_TWO_FAILURES, PYTEST_ONE_FAILURE, PYTEST_TWO_FAILURES, MYPY_TWO_ERRORS]
    )

    report = await repair_loop(verify=script.verify, fix=script.fix)

    notes = [attempt.note for attempt in report.attempts]
    assert "different failure, fewer of them (2 → 1)" in notes
    assert "different failure and MORE of them (1 → 2)" in notes


async def test_the_cap_is_respected_exactly() -> None:
    script = _Script([PYTEST_ONE_FAILURE, PYTEST_OTHER_FAILURE, RUFF_ONE_ERROR, MYPY_TWO_ERRORS])
    report = await repair_loop(verify=script.verify, fix=script.fix, max_attempts=2)
    assert len(report.attempts) == 2
    assert script.fix_calls == [1, 2]


async def test_a_cap_below_one_is_a_programming_error() -> None:
    script = _Script([PYTEST_ONE_FAILURE])
    with pytest.raises(ValueError, match="at least one attempt"):
        await repair_loop(verify=script.verify, fix=script.fix, max_attempts=0)


async def test_a_fixer_that_changes_nothing_is_still_recorded_as_an_attempt() -> None:
    calls = {"n": 0}

    async def verify() -> FailureSnapshot:
        calls["n"] += 1
        return _snapshot(PYTEST_ONE_FAILURE)

    async def fix(snapshot: FailureSnapshot, attempt: int) -> str:
        del snapshot, attempt
        return ""

    report = await repair_loop(verify=verify, fix=fix)

    assert report.verdict is RepairVerdict.STUCK
    assert "(no change was produced)" in report.render()
