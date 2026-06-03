"""Tests for the coverage gap finder."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.coverage import find_gaps, public_symbols, to_tasks


def test_public_symbols() -> None:
    src = "def foo():\n    pass\nclass Bar:\n    pass\ndef _private():\n    pass\n"
    assert public_symbols(src) == ["foo", "Bar"]


def test_public_symbols_dedup() -> None:
    assert public_symbols("def x():\n pass\ndef x():\n pass\n") == ["x"]


def test_find_gaps_basic(tmp_path: Path) -> None:
    src = tmp_path / "pkg"
    src.mkdir()
    (src / "mod.py").write_text("def covered():\n    pass\ndef uncovered():\n    pass\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text("from pkg.mod import covered\ndef test_it():\n    covered()\n", encoding="utf-8")
    gaps = find_gaps(tmp_path)
    syms = {g.symbol for g in gaps}
    assert "uncovered" in syms and "covered" not in syms


def test_find_gaps_word_boundary(tmp_path: Path) -> None:
    # a symbol 'add' must not be considered covered by the word 'address' in a test
    (tmp_path / "m.py").write_text("def add():\n    pass\n", encoding="utf-8")
    (tmp_path / "test_m.py").write_text("address = 1\n", encoding="utf-8")
    assert {g.symbol for g in find_gaps(tmp_path)} == {"add"}


def test_find_gaps_skips_private_and_vendor(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def _hidden():\n    pass\n", encoding="utf-8")
    vend = tmp_path / "node_modules"
    vend.mkdir()
    (vend / "v.py").write_text("def vendored():\n    pass\n", encoding="utf-8")
    assert find_gaps(tmp_path) == []


def test_to_tasks(tmp_path: Path) -> None:
    from ronin_cli.coverage import Gap
    tasks = to_tasks([Gap("foo", "a.py")])
    assert len(tasks) == 1 and tasks[0].source == "coverage"
    assert "foo" in tasks[0].title and "a.py" in tasks[0].detail
