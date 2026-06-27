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
