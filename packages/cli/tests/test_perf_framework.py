"""Deterministic coverage for persisted command benchmark reports."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.main import app
from ronin_cli.perf import (
    BenchmarkReport,
    CommandSample,
    compare_reports,
    load_report,
    run_benchmark,
    save_report,
)


def _report(*durations: float, failures: int = 0) -> BenchmarkReport:
    samples = tuple(
        CommandSample(duration=duration, exit_code=(1 if index < failures else 0))
        for index, duration in enumerate(durations)
    )
    return BenchmarkReport(
        command="pytest -q",
        root="/repo",
        runs=len(samples),
        warmup=0,
        timeout=60,
        samples=samples,
        created_at="2026-07-27T00:00:00+00:00",
        environment={"python": "3.14", "platform": "test", "machine": "test"},
    )


def test_run_benchmark_has_deterministic_phases_and_summary(tmp_path: Path) -> None:
    values = iter([
        CommandSample(0.5, 0),
        CommandSample(1.0, 0),
        CommandSample(1.4, 1, output_tail="failed"),
    ])

    report = run_benchmark(
        "fake", runs=2, warmup=1, root=tmp_path,
        executor=lambda _command, _root, _timeout: next(values),
        created_at="2026-07-27T00:00:00+00:00",
    )

    assert [sample.phase for sample in report.samples] == ["warmup", "timed", "timed"]
    assert report.summary() == {
        "runs": 2,
        "min": 1.0,
        "mean": 1.2,
        "median": 1.2,
        "max": 1.4,
        "stdev": 0.19999999999999996,
        "p95": 1.4,
        "failures": 1,
        "successful_runs": 1,
        "timed_out": 0,
    }
    assert report.last_output == "failed"


def test_report_round_trip_is_schema_validated_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "report.json"
    report = _report(1.0, 1.2)

    assert save_report(report, path) == path
    assert load_report(path) == report
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["summary"]["median"] == 1.1


def test_report_does_not_persist_captured_command_output(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    report = replace(_report(1.0), samples=(CommandSample(1.0, 0, output_tail="captured output"),))

    save_report(report, path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "output_tail" not in raw["samples"][0]
    assert load_report(path).last_output == ""


def test_report_refuses_corrupt_schema_and_sample_counts(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        load_report(path)

    raw = _report(1.0).to_dict()
    raw["runs"] = 2
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="timed sample count"):
        load_report(path)


def test_comparison_flags_latency_and_failure_regressions() -> None:
    baseline = _report(1.0, 1.0, 1.0)

    slower = compare_reports(baseline, _report(1.2, 1.2, 1.2), max_regression_percent=10)
    assert slower.status == "regressed"
    assert slower.delta_percent == 19.999999999999996

    failed = compare_reports(baseline, _report(0.8, 0.8, 0.8, failures=1))
    assert failed.status == "regressed"
    assert "more failed" in failed.reason

    improved = compare_reports(baseline, _report(0.8, 0.8, 0.8))
    assert improved.status == "improved"


def test_cli_saves_json_and_fails_a_regressed_baseline(tmp_path: Path, monkeypatch) -> None:
    import ronin_cli.perf as perf_module

    baseline_path = tmp_path / "baseline.json"
    json_out = tmp_path / "candidate.json"
    save_report(_report(1.0, 1.0), baseline_path)
    monkeypatch.setattr(perf_module, "run_benchmark", lambda *args, **kwargs: _report(1.3, 1.3))

    result = CliRunner().invoke(app, [
        "dev", "perf", "true", "--runs", "2", "--warmup", "0",
        "--baseline", str(baseline_path), "--json-out", str(json_out),
        "--max-regression-percent", "10",
    ])

    assert result.exit_code == 1
    assert "baseline regressed" in result.stdout
    assert load_report(json_out).summary()["median"] == 1.3
