"""Tests for Wave 6 independent verification (gated, honest)."""
from __future__ import annotations

import subprocess

from ronin_cli.pipeline_verify import (
    VerifyRun,
    independent_verify,
    reconcile_with_tester,
)


def _fake_run(returncode: int, out: str = ""):
    def run(command, *, cwd, timeout):
        return subprocess.CompletedProcess(args=command, returncode=returncode,
                                           stdout=out, stderr="")
    return run


def test_no_command_is_not_requested(tmp_path) -> None:
    run = independent_verify(None, tmp_path)
    assert run.requested is False
    assert run.verdict() == "not_provided"


def test_independent_verify_passes(tmp_path) -> None:
    run = independent_verify("pytest -q", tmp_path, approve_fn=lambda c: True,
                             run_fn=_fake_run(0, "5 passed"))
    assert run.ran and run.passed is True
    assert run.exit_code == 0 and run.verdict() == "passed"
    assert "passed" in run.output_summary


def test_independent_verify_fails(tmp_path) -> None:
    run = independent_verify("pytest -q", tmp_path, approve_fn=lambda c: True,
                             run_fn=_fake_run(1, "1 failed"))
    assert run.passed is False and run.verdict() == "failed"


def test_declined_command_is_blocked_not_passed(tmp_path) -> None:
    run = independent_verify("pytest -q", tmp_path, approve_fn=lambda c: False,
                             run_fn=_fake_run(0))
    assert run.declined is True and run.ran is False
    assert run.verdict() == "blocked"  # never 'passed' when not run


def test_yolo_skips_approval(tmp_path) -> None:
    calls = {"approved": 0}

    def approve(cmd):
        calls["approved"] += 1
        return False  # would decline if asked

    run = independent_verify("true", tmp_path, yolo=True, approve_fn=approve,
                             run_fn=_fake_run(0))
    assert calls["approved"] == 0   # not asked under yolo
    assert run.ran and run.verdict() == "passed"


def test_timeout_is_blocked(tmp_path) -> None:
    def boom(command, *, cwd, timeout):
        raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)
    run = independent_verify("sleep 100", tmp_path, approve_fn=lambda c: True, run_fn=boom)
    assert run.timed_out is True and run.verdict() == "blocked"


def test_real_command_runs(tmp_path) -> None:
    # actually shell out to a trivial true/false to prove the default path works
    assert independent_verify("true", tmp_path, yolo=True).verdict() == "passed"
    assert independent_verify("false", tmp_path, yolo=True).verdict() == "failed"


def test_reconcile_flags_tester_overclaim() -> None:
    failed = VerifyRun(requested=True, ran=True, passed=False)
    assert "FAILED" in reconcile_with_tester(failed, "passed")
    blocked = VerifyRun(requested=True, declined=True)
    assert "could not run" in reconcile_with_tester(blocked, "passed")
    # agreement → no flag
    passed = VerifyRun(requested=True, ran=True, passed=True)
    assert reconcile_with_tester(passed, "passed") is None
    # no independent run → nothing to reconcile
    assert reconcile_with_tester(VerifyRun(requested=False), "passed") is None


def test_verify_run_round_trips() -> None:
    run = independent_verify("pytest", ".", yolo=True, run_fn=_fake_run(0, "ok"))
    again = VerifyRun.model_validate_json(run.model_dump_json())
    assert again.passed is True and again.command == "pytest"


# --- Wave 7: auto-detected verify command ------------------------------------

from ronin_cli.pipeline_verify import auto_detect_verify_command


def test_auto_detect_uv_pytest(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    detected = auto_detect_verify_command(tmp_path)
    assert detected is not None
    cmd, runner = detected
    assert runner == "pytest"
    assert "pytest" in cmd  # "uv run pytest -q" (or bare "pytest -q")


def test_auto_detect_node(tmp_path) -> None:
    import json as _json
    (tmp_path / "package.json").write_text(
        _json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
    cmd, runner = auto_detect_verify_command(tmp_path)
    assert runner == "node" and "test" in cmd


def test_auto_detect_cargo(tmp_path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    cmd, runner = auto_detect_verify_command(tmp_path)
    assert (cmd, runner) == ("cargo test", "cargo")


def test_auto_detect_go(tmp_path) -> None:
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    cmd, runner = auto_detect_verify_command(tmp_path)
    assert cmd == "go test ./..." and runner == "go"


def test_auto_detect_memory_verify_line(tmp_path) -> None:
    (tmp_path / "RONIN.md").write_text("verify: mvn test\n", encoding="utf-8")
    cmd, runner = auto_detect_verify_command(tmp_path)
    assert cmd == "mvn test"


def test_auto_detect_none_in_empty_dir(tmp_path) -> None:
    assert auto_detect_verify_command(tmp_path) is None


# --- Wave 8: multi-suite verification ----------------------------------------

from ronin_cli.pipeline_verify import (
    SuiteRun,
    aggregate_verify,
    auto_detect_suites,
    parse_verify_suite,
    run_suites,
)


def test_parse_verify_suite() -> None:
    assert parse_verify_suite("unit:uv run pytest -q") == ("unit", "uv run pytest -q")
    assert parse_verify_suite("pnpm test") == ("verify", "pnpm test")
    assert parse_verify_suite(" lint : ruff check . ") == ("lint", "ruff check .")


def test_run_suites_aggregates(tmp_path) -> None:
    def fake(returncode):
        import subprocess
        return lambda command, *, cwd, timeout: subprocess.CompletedProcess(command, returncode, "", "")
    # two suites: unit passes, lint fails
    runs = []
    for name, cmd, rc in [("unit", "pytest", 0), ("lint", "ruff", 1)]:
        from ronin_cli.pipeline_verify import run_suite
        runs.append(run_suite(name, cmd, tmp_path, yolo=True, run_fn=fake(rc), clock=lambda: 0.0))
    assert [r.status for r in runs] == ["passed", "failed"]
    agg = aggregate_verify(runs)
    assert agg.verdict() == "failed"  # any failed suite → failed


def test_failed_suite_fails_final_verdict() -> None:
    suites = [SuiteRun(name="unit", status="passed"), SuiteRun(name="lint", status="failed")]
    assert aggregate_verify(suites).verdict() == "failed"


def test_declined_suite_blocks_not_passes() -> None:
    suites = [SuiteRun(name="unit", status="passed"),
              SuiteRun(name="lint", status="blocked", declined=True)]
    assert aggregate_verify(suites).verdict() == "blocked"


def test_all_passed_suites_pass() -> None:
    suites = [SuiteRun(name="unit", status="passed"), SuiteRun(name="lint", status="passed")]
    assert aggregate_verify(suites).verdict() == "passed"


def test_run_suites_declined_halts_rest(tmp_path) -> None:
    calls = []

    def approve(cmd):
        calls.append(cmd)
        return False  # decline everything

    runs = run_suites([("unit", "pytest"), ("lint", "ruff")], tmp_path,
                      approve_fn=approve, run_fn=lambda *a, **k: None, clock=lambda: 0.0)
    assert len(runs) == 1 and runs[0].declined is True  # stopped after the first decline
    assert calls == ["pytest"]


def test_auto_detect_suites_python(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    suites = auto_detect_suites(tmp_path)
    names = {n for n, _ in suites}
    assert "tests" in names and "lint" in names  # pytest + ruff detected
