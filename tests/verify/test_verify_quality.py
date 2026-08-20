"""Flaky detection, suite tiers, and artifact-contract validation.

All three are pure: flaky detection takes the test runner as an injected callable, suite
tiering folds hand-built results, and contract validation reads a ``tmp_path`` written by
hand. No real test runs, no subprocess, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ronin.verify.contract import ArtifactContract, ArtifactRule, validate_contract
from ronin.verify.flaky import Stability, classify, probe
from ronin.verify.suites import SuiteResult, SuiteTier, TieredOutcome, aggregate

# --------------------------------------------------------------------------- #
# flaky detection
# --------------------------------------------------------------------------- #


def test_classify_stable_and_flaky() -> None:
    assert classify([True, True, True]) is Stability.STABLE_PASS
    assert classify([False, False]) is Stability.STABLE_FAIL
    assert classify([True, False, True]) is Stability.FLAKY


def test_classify_rejects_no_runs() -> None:
    with pytest.raises(ValueError, match="at least one run"):
        classify([])


def test_probe_runs_exactly_n_times_and_flags_a_flake() -> None:
    scripted = iter([True, False, True])
    calls = {"n": 0}

    def run() -> bool:
        calls["n"] += 1
        return next(scripted)

    result = probe("tests/test_x.py::test_y", run, attempts=3)
    assert calls["n"] == 3
    assert result.is_flaky and result.passes == 2
    assert "flaky" in result.summary()


def test_probe_needs_at_least_two_attempts() -> None:
    with pytest.raises(ValueError, match="at least 2 attempts"):
        probe("t", lambda: True, attempts=1)


# --------------------------------------------------------------------------- #
# suite tiers
# --------------------------------------------------------------------------- #


def test_optional_failure_does_not_fail_the_gate() -> None:
    outcome = aggregate(
        [
            SuiteResult("pytest", SuiteTier.REQUIRED, passed=True),
            SuiteResult("mypy", SuiteTier.OPTIONAL, passed=False, detail="3 errors"),
        ]
    )
    assert outcome.gate_passed  # required passed → gate green
    assert [r.name for r in outcome.optional_failures] == ["mypy"]
    assert "advisory" in outcome.summary()


def test_required_failure_fails_the_gate() -> None:
    outcome = aggregate(
        [
            SuiteResult("pytest", SuiteTier.REQUIRED, passed=False),
            SuiteResult("lint", SuiteTier.OPTIONAL, passed=True),
        ]
    )
    assert not outcome.gate_passed
    assert [r.name for r in outcome.required_failures] == ["pytest"]
    assert "gate FAILED" in outcome.summary()


def test_empty_outcome_passes() -> None:
    assert TieredOutcome().gate_passed


# --------------------------------------------------------------------------- #
# artifact contract
# --------------------------------------------------------------------------- #


def test_contract_satisfied_when_files_and_keys_present(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text(json.dumps({"score": 9, "model": "x"}), encoding="utf-8")
    (tmp_path / "out.txt").write_text("done", encoding="utf-8")
    contract = ArtifactContract(
        rules=(
            ArtifactRule("report.json", json_keys=("score", "model")),
            ArtifactRule("out.txt"),
        )
    )
    result = validate_contract(contract, tmp_path)
    assert result.satisfied and "satisfied" in result.summary()


def test_contract_flags_missing_file_and_missing_key(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text(json.dumps({"score": 9}), encoding="utf-8")
    contract = ArtifactContract(
        rules=(
            ArtifactRule("report.json", json_keys=("score", "model")),  # missing 'model'
            ArtifactRule("missing.txt"),  # missing file
        )
    )
    result = validate_contract(contract, tmp_path)
    reasons = {v.path: v.reason for v in result.violations}
    assert "missing key(s): model" in reasons["report.json"]
    assert reasons["missing.txt"] == "missing"
    assert "2 violation(s)" in result.summary()


def test_contract_flags_non_json_and_non_object(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("not json{", encoding="utf-8")
    (tmp_path / "arr.json").write_text("[1, 2, 3]", encoding="utf-8")
    contract = ArtifactContract(
        rules=(
            ArtifactRule("bad.json", json_keys=("x",)),
            ArtifactRule("arr.json", json_keys=("x",)),
        )
    )
    result = validate_contract(contract, tmp_path)
    reasons = {v.path: v.reason for v in result.violations}
    assert "not valid json" in reasons["bad.json"]
    assert reasons["arr.json"] == "json is not an object"


def test_optional_missing_artifact_is_not_a_violation(tmp_path: Path) -> None:
    contract = ArtifactContract(rules=(ArtifactRule("maybe.txt", must_exist=False),))
    assert validate_contract(contract, tmp_path).satisfied
