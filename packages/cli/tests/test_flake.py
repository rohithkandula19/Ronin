"""Tests for `ronin flake` — outcome classification + pytest failure parsing."""
from __future__ import annotations

from ronin_cli.flake import classify, flaky_tests, parse_pytest_failures, run_flake


def test_classify() -> None:
    assert classify([True, True, True]) == "stable-pass"
    assert classify([False, False]) == "stable-fail"
    assert classify([True, False, True]) == "flaky"
    assert classify([]) == "unknown"


def test_parse_pytest_failures_both_forms() -> None:
    summary = "FAILED tests/test_a.py::test_one - AssertionError\nERROR tests/test_b.py::test_two"
    verbose = "tests/test_c.py::test_three FAILED\ntests/test_a.py::test_one PASSED"
    assert parse_pytest_failures(summary) == {
        "tests/test_a.py::test_one", "tests/test_b.py::test_two"}
    assert "tests/test_c.py::test_three" in parse_pytest_failures(verbose)


def test_flaky_tests_excludes_stable_failures() -> None:
    # t_flaky fails in run 1 only; t_broken fails in both → only t_flaky is flaky.
    sets = [{"t_flaky", "t_broken"}, {"t_broken"}]
    assert flaky_tests(sets) == {"t_flaky"}
    assert flaky_tests([set(), set()]) == set()


def test_run_flake_detects_a_real_flaky_test(tmp_path) -> None:
    # A test that fails exactly on the 2nd invocation, tracked via a counter file.
    counter = tmp_path / "n.txt"
    (tmp_path / "test_flaky.py").write_text(
        "from pathlib import Path\n"
        f"c = Path(r'{counter}')\n"
        "def test_sometimes():\n"
        "    n = int(c.read_text()) if c.exists() else 0\n"
        "    c.write_text(str(n + 1))\n"
        "    assert n != 1\n",
        encoding="utf-8",
    )
    res = run_flake(tmp_path, "python -m pytest -q", runs=3)
    assert res.verdict == "flaky"
    assert any("test_sometimes" in t for t, _ in res.flaky)
