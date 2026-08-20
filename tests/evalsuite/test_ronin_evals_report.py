"""Reports: real numbers only, ``—`` where there is none, and a diffable machine record."""

from __future__ import annotations

import json

import pytest
from evals_harness import call, probe, record

from ronin.evals.report import (
    ABSENT,
    MISSING_CELL,
    ROW_OUTPUT_CHARS,
    build_report,
    distribution,
    report_json,
    report_markdown,
    report_payload,
    scoreboard,
    scoreboard_markdown,
)
from ronin.evals.taxonomy import (
    AgentOutcome,
    FailureClass,
    RunRecord,
    TaskResult,
    UsageRecord,
    judge,
)


def passing(task_id: str, category: str = "edit", **kwargs: object) -> TaskResult:
    return judge(record(task_id=task_id, category=category, verify_after=probe(0), **kwargs))  # type: ignore[arg-type]


def failing(task_id: str, category: str = "edit", **kwargs: object) -> TaskResult:
    kwargs.setdefault("verify_after", probe(1))
    return judge(record(task_id=task_id, category=category, **kwargs))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #


def test_an_empty_report_has_no_pass_rate_rather_than_zero_percent() -> None:
    report = build_report([])
    assert report.overall.pass_rate is None
    assert report.overall.render_pass_rate() == ABSENT
    assert report_payload(report)["overall"]["pass_rate"] is None


def test_skipped_tasks_are_excluded_from_the_pass_rate_denominator() -> None:
    results = [
        passing("a"),
        failing("b"),
        judge(record(task_id="c", skipped=True, skip_reason="needs network")),
    ]
    report = build_report(results)
    assert report.overall.tasks == 3
    assert report.overall.skipped == 1
    assert report.overall.attempted == 2
    assert report.overall.pass_rate == 0.5


def test_aggregates_are_computed_per_category() -> None:
    report = build_report([passing("a", "edit"), failing("b", "edit"), passing("c", "ask")])
    assert report.per_category["edit"].pass_rate == 0.5
    assert report.per_category["ask"].pass_rate == 1.0


def test_rows_are_ordered_by_category_then_id_whatever_order_they_finished_in() -> None:
    report = build_report([passing("z", "edit"), passing("a", "ask"), passing("m", "edit")])
    assert [row.task_id for row in report.rows] == ["a", "m", "z"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((), (0, 0.0, 0.0)),
        ((5.0,), (1, 5.0, 5.0)),
        ((1.0, 2.0, 3.0, 4.0), (4, 2.5, 4.0)),
    ],
)
def test_a_distribution_over_nothing_is_empty_rather_than_zeros(
    values: tuple[float, ...], expected: tuple[int, float, float]
) -> None:
    dist = distribution("x", values)
    count, median, p90 = expected
    assert dist.count == count
    assert dist.measured is (count > 0)
    if count:
        assert dist.median == median
        assert dist.p90 == p90
    else:
        assert dist.render() == ABSENT


# --------------------------------------------------------------------------- #
# Taxonomy in the report
# --------------------------------------------------------------------------- #


def test_the_unclassified_count_is_reported_next_to_the_classes() -> None:
    mystery = failing(
        "mystery",
        tool_calls=[call("read")],
        edited_paths=["app.py"],
        files_expected=["app.py"],
        final_text="I fixed it.",
    )
    known = failing(
        "known",
        tool_calls=[call("edit", ok=False, error="old_string appears 3 times in app.py")],
    )
    report = build_report([mystery, known])
    assert report.taxonomy.failures == 2
    assert report.taxonomy.unclassified == 1
    assert report.taxonomy.classified == 1
    assert report.taxonomy.counts[FailureClass.EDIT_AMBIGUITY] == 1
    markdown = report_markdown(report)
    assert "unclassified failures: 1/2 (50%)" in markdown
    assert "error bar" in markdown


def test_the_breakdown_says_which_side_of_the_system_to_fix() -> None:
    report = build_report(
        [failing("h", tool_calls=[call("nonexistent_tool")], registered_tools=["read"])]
    )
    assert report.taxonomy.blame["harness"] == 1
    assert "harness" in report_markdown(report)


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #


def test_the_json_record_has_a_fixed_key_order() -> None:
    report = build_report([passing("a"), failing("b")], label="run", model="fake")
    first = report_json(report)
    second = report_json(build_report([failing("b"), passing("a")], label="run", model="fake"))
    assert first == second
    assert list(json.loads(first)) == [
        "schema",
        "label",
        "model",
        "suite_root",
        "overall",
        "per_category",
        "taxonomy",
        "distributions",
        "tasks",
    ]


def test_timings_can_be_dropped_so_two_machines_compare_on_behaviour() -> None:
    report = build_report([passing("a", wall_seconds=3.5)])
    with_timings = json.loads(report_json(report))
    without = json.loads(report_json(report, include_timings=False))
    assert with_timings["tasks"][0]["wall_seconds"] == 3.5
    assert "wall_seconds" not in without["tasks"][0]
    assert "wall_seconds" not in without["distributions"]


def test_every_failure_class_appears_in_the_json_even_at_zero() -> None:
    """A class missing from the record is indistinguishable from a class not measured."""
    payload = report_payload(build_report([passing("a")]))
    assert set(payload["taxonomy"]["counts"]) == {member.value for member in FailureClass}


def test_unreported_usage_reaches_the_json_as_a_word_not_a_zero() -> None:
    result = TaskResult(
        record=RunRecord(
            task_id="a",
            category="edit",
            outcome=AgentOutcome(usage=UsageRecord(reported=False)),
            verify_after=probe(0),
        )
    )
    payload = report_payload(build_report([result]))
    assert payload["tasks"][0]["tokens"] == "unreported"
    assert payload["tasks"][0]["cost"] == "unreported"


def test_an_unpriced_but_reported_run_says_its_cost_is_a_floor() -> None:
    result = TaskResult(
        record=RunRecord(
            task_id="a",
            outcome=AgentOutcome(usage=UsageRecord(reported=True, priced=False, tokens=10)),
            verify_after=probe(0),
        )
    )
    row = build_report([result]).rows[0]
    assert row.tokens == "10"
    assert "floor, unpriced" in row.cost


# --------------------------------------------------------------------------- #
# Truncation
# --------------------------------------------------------------------------- #


def test_captured_output_is_clamped_deterministically_with_a_marker() -> None:
    huge = "\n".join(f"line {index}" for index in range(4_000))
    report = build_report([failing("a", verify_after=probe(1, huge))])
    row = report.rows[0]
    assert len(row.verify_output) <= ROW_OUTPUT_CHARS + 200
    assert "lines elided" in row.verify_output
    # Head and tail both survive, so a failure at the end of a log is still visible.
    assert row.verify_output.startswith("line 0")
    assert row.verify_output.rstrip().endswith("line 3999")
    again = build_report([failing("a", verify_after=probe(1, huge))])
    assert again.rows[0].verify_output == row.verify_output


def test_short_output_passes_through_byte_identical() -> None:
    text = "FAILED tests/test_a.py::test_x\n1 failed\n"
    report = build_report([failing("a", verify_after=probe(1, text))])
    assert report.rows[0].verify_output == text


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #


def test_markdown_prints_an_em_dash_where_a_number_does_not_exist() -> None:
    markdown = report_markdown(build_report([]))
    assert f"pass rate: {ABSENT}" in markdown
    assert "0.0%" not in markdown


def test_markdown_quotes_the_evidence_for_every_failure() -> None:
    ambiguous = call("edit", ok=False, error="old_string appears 3 times in app.py")
    markdown = report_markdown(build_report([failing("a", tool_calls=[ambiguous])]))
    assert "## evidence" in markdown
    assert "edit_ambiguity" in markdown
    assert "old_string appears 3 times" in markdown
    assert "fix the harness" in markdown


def test_markdown_ends_with_exactly_one_newline() -> None:
    markdown = report_markdown(build_report([passing("a")]))
    assert markdown.endswith("\n")
    assert not markdown.endswith("\n\n")


# --------------------------------------------------------------------------- #
# Scoreboard
# --------------------------------------------------------------------------- #


def test_a_scoreboard_names_regressions_and_fixes_against_the_first_run() -> None:
    base = build_report([passing("keeps"), passing("breaks"), failing("gets-fixed")], label="base")
    candidate = build_report(
        [passing("keeps"), failing("breaks"), passing("gets-fixed")], label="adapter"
    )
    board = scoreboard([base, candidate])
    assert board.labels == ("base", "adapter")
    assert board.regressions == ("breaks",)
    assert board.fixes == ("gets-fixed",)
    assert board.incomparable == ()
    row = next(item for item in board.rows if item.task_id == "breaks")
    assert row.outcomes == ("pass", "fail")
    assert row.moved is True


def test_a_task_missing_from_one_run_is_named_not_silently_intersected() -> None:
    left = build_report([passing("shared"), passing("only-left")], label="left")
    right = build_report([failing("shared")], label="right")
    board = scoreboard([left, right])
    assert board.incomparable == ("only-left",)
    assert board.regressions == ("shared",)
    row = next(item for item in board.rows if item.task_id == "only-left")
    assert row.outcomes == ("pass", MISSING_CELL)
    assert "not in every run" in scoreboard_markdown(board)


def test_a_scoreboard_over_three_runs_keeps_the_columns_aligned() -> None:
    reports = [
        build_report([passing("a"), failing("b")], label=name) for name in ("one", "two", "three")
    ]
    board = scoreboard(reports)
    assert board.labels == ("one", "two", "three")
    assert all(len(row.outcomes) == 3 for row in board.rows)
    markdown = scoreboard_markdown(board)
    assert "| id | category | one | two | three |" in markdown


def test_an_empty_scoreboard_says_so_instead_of_rendering_an_empty_table() -> None:
    assert "no runs to compare" in scoreboard_markdown(scoreboard([]))


def test_the_scoreboard_is_a_pure_function_of_the_reports() -> None:
    reports = [build_report([passing("a")], label="x"), build_report([failing("a")], label="y")]
    assert scoreboard(reports) == scoreboard(reports)
