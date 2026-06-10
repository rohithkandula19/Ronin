"""Tests for Kaizen's pure, verifiable core — weakness finder + fitness parser.

The drafting step needs a live model and runs sandboxed in a worktree, so it's
not unit-tested here; the deterministic pieces that decide *what* to improve and
*whether it improved* are.
"""
from __future__ import annotations

from pathlib import Path

from rich.console import Console

from ronin_cli.config import RoninConfig
from ronin_cli.kaizen import KaizenResult, find_targets, parse_pytest, run_kaizen


def test_find_targets_picks_up_markers(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "x = 1  # TODO: make configurable\n"
        "def f():\n"
        "    pass  # FIXME: handle empty input\n",
        encoding="utf-8",
    )
    targets = find_targets(tmp_path)
    markers = {t.marker for t in targets}
    assert "TODO" in markers and "FIXME" in markers
    # FIXME outranks TODO → comes first
    assert targets[0].marker == "FIXME"
    assert targets[0].line == 3


def test_find_targets_skips_vendor_dirs(tmp_path: Path) -> None:
    vendored = tmp_path / "node_modules"
    vendored.mkdir()
    (vendored / "lib.js").write_text("// TODO: ignore me\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("# TODO: find me\n", encoding="utf-8")
    targets = find_targets(tmp_path)
    files = {t.file for t in targets}
    assert "real.py" in files
    assert not any("node_modules" in f for f in files)


def test_find_targets_empty(tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    assert find_targets(tmp_path) == []


def test_find_targets_ignores_string_literals_and_docstrings(tmp_path: Path) -> None:
    # the marker word appearing in code/strings/docstrings is NOT a real target —
    # only actual comment markers count (this was a real false-positive bug)
    (tmp_path / "a.py").write_text(
        '_MARKERS = ("FIXME", "BUG", "TODO")\n'
        '"""This docstring mentions TODO/FIXME markers but is not one."""\n'
        'msg = "no FIXME here either"\n'
        'x = 1  # TODO: this one is real\n',
        encoding="utf-8",
    )
    targets = find_targets(tmp_path)
    assert len(targets) == 1
    assert targets[0].marker == "TODO" and targets[0].line == 4
    assert "this one is real" in targets[0].text


def test_parse_pytest_all_passed() -> None:
    f = parse_pytest("........\n534 passed, 5 warnings in 7.55s")
    assert f.passed and f.total == 534 and f.failed == 0


def test_parse_pytest_with_failures() -> None:
    f = parse_pytest("F...\n2 failed, 530 passed in 6.1s")
    assert not f.passed
    assert f.failed == 2 and f.total == 532


def test_parse_pytest_errors_count_as_failed() -> None:
    f = parse_pytest("1 error, 10 passed in 1s")
    assert not f.passed and f.failed == 1


def test_parse_pytest_no_output() -> None:
    f = parse_pytest("")
    assert not f.passed and f.total == 0


def test_kaizen_result_has_verdict_field() -> None:
    # the Duel-into-Kaizen wiring adds an optional verdict, defaulting to None
    r = KaizenResult(None, None, "", False, "x")
    assert r.verdict is None


def test_run_kaizen_outside_git_is_clean(tmp_path: Path) -> None:
    # the no-git early return must still produce a well-formed result (verdict None)
    res = run_kaizen(RoninConfig(), tmp_path, Console(), goal="anything",
                     duel_against={"provider": "gemini"})
    assert not res.applied and res.verdict is None
    assert "git" in res.note.lower()
