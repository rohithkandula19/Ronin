"""Tests for lint (detect/parse) and checkup (repo health)."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.checkup import Check, grade, health_score, run_checks
from ronin_cli.lint import detect_linter, fix_command, parse_issue_count


# ---- lint ----

def test_detect_ruff_preferred(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert detect_linter(tmp_path, available={"ruff", "flake8"}) == ["ruff", "check", "."]


def test_detect_falls_back_to_flake8(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert detect_linter(tmp_path, available={"flake8"}) == ["flake8"]


def test_detect_eslint_for_js(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert detect_linter(tmp_path, available={"npx"}) == ["npx", "eslint", "."]


def test_detect_none_when_nothing(tmp_path: Path) -> None:
    assert detect_linter(tmp_path, available=set()) is None


def test_fix_command() -> None:
    assert fix_command(["ruff", "check", "."]) == ["ruff", "check", "--fix", "."]
    assert fix_command(["npx", "eslint", "."]) == ["npx", "eslint", ".", "--fix"]
    assert fix_command(["flake8"]) is None


def test_parse_ruff_count() -> None:
    assert parse_issue_count("Found 7 errors.", ["ruff"]) == 7
    assert parse_issue_count("All checks passed!", ["ruff"]) == 0


def test_parse_flake8_and_eslint() -> None:
    f8 = "a.py:1:1: F401 unused\na.py:2:5: E303 blank\n"
    assert parse_issue_count(f8, ["flake8"]) == 2
    assert parse_issue_count("✖ 12 problems (3 errors, 9 warnings)", ["npx"]) == 12


# ---- checkup ----

def test_run_checks_healthy_repo(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# x", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("MIT", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    statuses = {c.name: c.status for c in run_checks(tmp_path)}
    assert statuses["readme"] == "ok"
    assert statuses["license"] == "ok"
    assert statuses["tests"] == "ok"
    assert statuses["secrets"] == "ok"


def test_run_checks_flags_missing(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    statuses = {c.name: c.status for c in run_checks(tmp_path)}
    assert statuses["readme"] == "warn"
    assert statuses["license"] == "warn"
    assert statuses["tests"] == "fail"


def test_health_score_and_grade() -> None:
    perfect = [Check("a", "ok", ""), Check("b", "ok", "")]
    assert health_score(perfect) == 100 and grade(100) == "A"
    mixed = [Check("a", "fail", ""), Check("b", "warn", ""), Check("c", "ok", "")]
    assert health_score(mixed) == 100 - 20 - 7
    assert grade(health_score(mixed)) in {"B", "C"}


def test_health_score_floored_at_zero() -> None:
    awful = [Check(f"c{i}", "fail", "") for i in range(10)]
    assert health_score(awful) == 0 and grade(0) == "F"
