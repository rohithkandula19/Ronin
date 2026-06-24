"""SWE-bench harness tests — fully offline (mock runner + mock evaluator)."""
from __future__ import annotations

import json

import pytest

from ronin_eval_suite import (
    SWEBenchDataset,
    SWEBenchHarness,
    SWEBenchReport,
    SWEBenchResult,
    SWEBenchTask,
    TaskEvaluation,
    compare_swebench,
    is_resolved,
    oracle_runner,
    render_swebench_markdown,
)
from ronin_eval_suite.swebench import _parse_pytest_results


def _task(instance_id: str = "demo__pkg-1", **kw) -> SWEBenchTask:
    base = dict(
        instance_id=instance_id,
        repo="demo/pkg",
        base_commit="abc123",
        problem_statement="fix the bug",
        fail_to_pass=["tests/test_x.py::test_new"],
        pass_to_pass=["tests/test_x.py::test_existing"],
    )
    base.update(kw)
    return SWEBenchTask(**base)


# --- task model / alias / coercion -------------------------------------------------

def test_task_accepts_official_uppercase_aliases_and_json_string_lists():
    line = json.dumps(
        {
            "instance_id": "django__django-1",
            "repo": "django/django",
            "base_commit": "deadbeef",
            "problem_statement": "x",
            # the published dataset ships these as JSON-encoded *strings*:
            "FAIL_TO_PASS": "[\"t.py::a\", \"t.py::b\"]",
            "PASS_TO_PASS": "[\"t.py::c\"]",
        }
    )
    task = SWEBenchTask.model_validate_json(line)
    assert task.fail_to_pass == ["t.py::a", "t.py::b"]
    assert task.pass_to_pass == ["t.py::c"]


def test_task_accepts_native_lists_and_snake_case():
    task = _task(fail_to_pass=["a"], pass_to_pass=["b", "c"])
    assert task.fail_to_pass == ["a"]
    assert task.pass_to_pass == ["b", "c"]


# --- dataset ----------------------------------------------------------------------

def test_dataset_from_jsonl_skips_blanks_and_comments(tmp_path):
    p = tmp_path / "tasks.jsonl"
    p.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                json.dumps({"instance_id": "i1", "FAIL_TO_PASS": "[\"t::a\"]"}),
                json.dumps({"instance_id": "i2", "FAIL_TO_PASS": "[\"t::b\"]"}),
            ]
        ),
        encoding="utf-8",
    )
    ds = SWEBenchDataset.from_jsonl(p)
    assert len(ds) == 2
    assert [t.instance_id for t in ds] == ["i1", "i2"]


def test_dataset_rejects_duplicate_instance_ids():
    with pytest.raises(ValueError, match="duplicate instance_id"):
        SWEBenchDataset([_task("dup"), _task("dup")])


def test_dataset_repos_are_sorted_and_unique():
    ds = SWEBenchDataset(
        [_task("a", repo="z/one"), _task("b", repo="a/two"), _task("c", repo="z/one")]
    )
    assert ds.repos() == ["a/two", "z/one"]


def test_dataset_subset_filters_by_ids_repo_and_limit():
    ds = SWEBenchDataset(
        [
            _task("a", repo="r/1"),
            _task("b", repo="r/1"),
            _task("c", repo="r/2"),
            _task("d", repo="r/1"),
        ]
    )
    assert [t.instance_id for t in ds.subset(repo="r/1")] == ["a", "b", "d"]
    assert [t.instance_id for t in ds.subset(ids=["b", "c"])] == ["b", "c"]
    assert [t.instance_id for t in ds.subset(limit=2)] == ["a", "b"]
    # filters compose: repo then limit
    assert [t.instance_id for t in ds.subset(repo="r/1", limit=2)] == ["a", "b"]
    # subset returns a fresh dataset, original untouched
    assert len(ds) == 4


# --- resolution rule --------------------------------------------------------------

def test_is_resolved_requires_all_fail_to_pass_and_pass_to_pass():
    task = _task()
    full = TaskEvaluation(passed_tests=task.fail_to_pass + task.pass_to_pass)
    assert is_resolved(task, full) is True


def test_is_resolved_false_when_a_pass_to_pass_regresses():
    task = _task()
    # FAIL_TO_PASS fixed, but the regression guard test now fails
    ev = TaskEvaluation(passed_tests=task.fail_to_pass, failed_tests=task.pass_to_pass)
    assert is_resolved(task, ev) is False


def test_is_resolved_false_when_fail_to_pass_missing():
    task = _task()
    ev = TaskEvaluation(passed_tests=task.pass_to_pass)  # FAIL_TO_PASS never passed
    assert is_resolved(task, ev) is False


def test_is_resolved_false_on_evaluator_error_even_if_tests_listed_passed():
    task = _task()
    ev = TaskEvaluation(passed_tests=task.fail_to_pass + task.pass_to_pass, error="boom")
    assert is_resolved(task, ev) is False


def test_is_resolved_false_when_no_required_tests():
    task = _task(fail_to_pass=[], pass_to_pass=[])
    assert is_resolved(task, TaskEvaluation(passed_tests=[])) is False


# --- harness orchestration --------------------------------------------------------

def test_harness_resolves_and_summarizes():
    task = _task()

    def runner(t: SWEBenchTask) -> str:
        return "diff --git a/x b/x\n+fix\n"

    def evaluator(t: SWEBenchTask, patch: str) -> TaskEvaluation:
        return TaskEvaluation(passed_tests=t.fail_to_pass + t.pass_to_pass)

    report = SWEBenchHarness(patch_runner=runner, evaluator=evaluator, model="ronin").run(
        SWEBenchDataset([task])
    )
    assert isinstance(report, SWEBenchReport)
    r = report.results[0]
    assert r.resolved is True
    assert r.patch_generated is True
    assert r.fail_to_pass_passed == r.fail_to_pass_total == 1
    assert r.pass_to_pass_passed == r.pass_to_pass_total == 1
    assert report.summary == {
        "total": 1.0,
        "resolved": 1.0,
        "resolved_rate": 1.0,
        "patch_generated": 1.0,
        "errored": 0.0,
    }


def test_harness_empty_patch_is_unresolved_and_skips_evaluator():
    calls = {"n": 0}

    def runner(t: SWEBenchTask) -> str:
        return "   "  # whitespace only == no patch

    def evaluator(t: SWEBenchTask, patch: str) -> TaskEvaluation:
        calls["n"] += 1
        return TaskEvaluation(passed_tests=t.fail_to_pass + t.pass_to_pass)

    report = SWEBenchHarness(patch_runner=runner, evaluator=evaluator).run(
        SWEBenchDataset([_task()])
    )
    assert calls["n"] == 0
    assert report.results[0].resolved is False
    assert report.results[0].patch_generated is False
    assert report.summary["patch_generated"] == 0.0


def test_harness_records_runner_failure_and_continues():
    def runner(t: SWEBenchTask) -> str:
        if t.instance_id == "boom":
            raise RuntimeError("agent crashed")
        return "diff\n"

    def evaluator(t: SWEBenchTask, patch: str) -> TaskEvaluation:
        return TaskEvaluation(passed_tests=t.fail_to_pass + t.pass_to_pass)

    ds = SWEBenchDataset([_task("boom"), _task("ok")])
    report = SWEBenchHarness(patch_runner=runner, evaluator=evaluator).run(ds)

    boom, ok = report.results
    assert boom.resolved is False and boom.error and "agent crashed" in boom.error
    assert ok.resolved is True
    assert report.summary["errored"] == 1.0
    assert report.summary["resolved"] == 1.0


def test_harness_records_evaluator_failure():
    def runner(t: SWEBenchTask) -> str:
        return "diff\n"

    def evaluator(t: SWEBenchTask, patch: str) -> TaskEvaluation:
        raise RuntimeError("git apply exploded")

    report = SWEBenchHarness(patch_runner=runner, evaluator=evaluator).run(
        SWEBenchDataset([_task()])
    )
    r = report.results[0]
    assert r.resolved is False
    assert r.patch_generated is True
    assert r.error and "git apply exploded" in r.error


def test_report_round_trips_through_json():
    report = SWEBenchHarness(
        patch_runner=lambda t: "diff\n",
        evaluator=lambda t, p: TaskEvaluation(passed_tests=t.fail_to_pass + t.pass_to_pass),
    ).run(SWEBenchDataset([_task()]))
    restored = SWEBenchReport.model_validate_json(report.model_dump_json())
    assert restored.summary == report.summary
    assert restored.results[0].instance_id == report.results[0].instance_id


# --- pytest output parsing --------------------------------------------------------

def test_parse_pytest_results_buckets_passed_failed_and_errors():
    stdout = "\n".join(
        [
            "PASSED tests/test_x.py::test_existing",
            "FAILED tests/test_x.py::test_new",
            "ERROR tests/test_x.py::test_collide",
            "PASSED tests/other.py::ignored",  # not requested -> dropped
        ]
    )
    wanted = [
        "tests/test_x.py::test_existing",
        "tests/test_x.py::test_new",
        "tests/test_x.py::test_collide",
    ]
    ev = _parse_pytest_results(stdout, wanted)
    assert ev.passed_tests == ["tests/test_x.py::test_existing"]
    assert set(ev.failed_tests) == {"tests/test_x.py::test_new", "tests/test_x.py::test_collide"}


def test_oracle_runner_returns_gold_patch_and_resolves_under_a_faithful_evaluator():
    task = _task(patch="diff --git a/fix b/fix\n+gold\n")
    assert oracle_runner(task) == task.patch

    # an evaluator that actually applies the gold patch should resolve the task
    def evaluator(t, patch):
        assert patch == t.patch  # the gold patch reached the evaluator
        return TaskEvaluation(passed_tests=t.fail_to_pass + t.pass_to_pass)

    report = SWEBenchHarness(patch_runner=oracle_runner, evaluator=evaluator).run(
        SWEBenchDataset([task])
    )
    assert report.results[0].resolved is True


def test_oracle_runner_empty_when_no_gold_patch():
    assert oracle_runner(_task(patch=None)) == ""


def _report(resolved_by_id: dict[str, bool], rate: float) -> SWEBenchReport:
    return SWEBenchReport(
        results=[
            SWEBenchResult(instance_id=i, resolved=r, patch_generated=True)
            for i, r in resolved_by_id.items()
        ],
        summary={"resolved_rate": rate},
    )


def test_compare_classifies_each_common_instance():
    base = _report({"a": True, "b": False, "c": True, "d": False}, 0.5)
    cand = _report({"a": True, "b": True, "c": False, "d": False}, 0.5)
    cmp = compare_swebench(base, cand)
    assert cmp.newly_resolved == ["b"]
    assert cmp.newly_broken == ["c"]
    assert cmp.still_resolved == ["a"]
    assert cmp.still_unresolved == ["d"]
    assert cmp.has_regression is True
    assert cmp.rate_delta == 0.0


def test_compare_ignores_instances_not_in_both_runs():
    base = _report({"a": True, "b": True}, 1.0)
    cand = _report({"a": True}, 1.0)  # partial run — 'b' absent
    cmp = compare_swebench(base, cand)
    assert cmp.still_resolved == ["a"]
    assert "b" not in (cmp.newly_broken + cmp.still_resolved + cmp.still_unresolved)
    assert cmp.has_regression is False


def test_compare_rate_delta():
    cmp = compare_swebench(_report({"a": True}, 0.30), _report({"a": True}, 0.42))
    assert cmp.rate_delta == 0.12


def test_render_markdown_has_headline_and_one_row_per_status():
    report = SWEBenchReport(
        label="ronin · gemini",
        results=[
            SWEBenchResult(
                instance_id="ok-1", resolved=True, patch_generated=True,
                fail_to_pass_passed=1, fail_to_pass_total=1,
                pass_to_pass_passed=2, pass_to_pass_total=2,
            ),
            SWEBenchResult(
                instance_id="bad-1", resolved=False, patch_generated=True,
                fail_to_pass_passed=0, fail_to_pass_total=1,
            ),
            SWEBenchResult(instance_id="none-1", resolved=False, patch_generated=False),
            SWEBenchResult(
                instance_id="err-1", resolved=False, patch_generated=True, error="git apply failed",
            ),
        ],
    )
    report.compute_summary()
    md = render_swebench_markdown(report)

    assert "## ronin · gemini — resolved 1/4 (25.0%)" in md
    assert "| `ok-1` | ✅ resolved | 1/1 | 2/2 |" in md
    assert "❌ unresolved" in md
    assert "∅ no patch" in md
    assert "⚠️ error" in md and "git apply failed" in md
    # header + separator + 4 data rows
    assert md.count("\n| `") == 4


def test_parse_pytest_results_omits_uncategorized_tests():
    # a test that neither PASSED nor FAILED (e.g. collection error, skipped) must
    # not count as passing -> resolution can never silently succeed.
    ev = _parse_pytest_results("PASSED a::b", ["a::b", "a::missing"])
    assert ev.passed_tests == ["a::b"]
    assert "a::missing" not in ev.passed_tests


def test_by_repo_groups_and_orders_by_resolved_rate():
    report = SWEBenchReport(
        results=[
            SWEBenchResult(instance_id="django__django-1", resolved=True, patch_generated=True),
            SWEBenchResult(instance_id="django__django-2", resolved=False, patch_generated=True),
            SWEBenchResult(instance_id="flask__flask-9", resolved=True, patch_generated=True),
        ]
    )
    breakdown = report.by_repo()
    assert breakdown["flask__flask"] == {"resolved": 1.0, "total": 1.0, "resolved_rate": 1.0}
    assert breakdown["django__django"] == {"resolved": 1.0, "total": 2.0, "resolved_rate": 0.5}
    # ordered by descending resolved_rate -> flask (1.0) before django (0.5)
    assert list(breakdown) == ["flask__flask", "django__django"]


def test_render_markdown_repo_breakdown_section_optional():
    report = SWEBenchReport(
        results=[
            SWEBenchResult(instance_id="django__django-1", resolved=True, patch_generated=True),
            SWEBenchResult(instance_id="flask__flask-9", resolved=False, patch_generated=True),
        ]
    )
    report.compute_summary()

    assert "### By repo" not in render_swebench_markdown(report)

    md = render_swebench_markdown(report, include_repo_breakdown=True)
    assert "### By repo" in md
    assert "| `django__django` | 1/1 | 100.0% |" in md
    assert "| `flask__flask` | 0/1 | 0.0% |" in md
