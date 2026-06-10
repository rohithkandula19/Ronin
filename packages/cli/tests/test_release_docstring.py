"""Tests for release (semver + version rewrite) and docstring detection."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli.docstring import find_doc_gaps, to_tasks, undocumented_in_source
from ronin_cli.release import (
    bump_version,
    current_version,
    find_version_files,
    set_version_in_text,
)


# ---- release ----

@pytest.mark.parametrize("cur,kind,new", [
    ("1.2.3", "patch", "1.2.4"),
    ("1.2.3", "minor", "1.3.0"),
    ("1.2.3", "major", "2.0.0"),
    ("0.57.0", "minor", "0.58.0"),
])
def test_bump_version(cur, kind, new) -> None:
    assert bump_version(cur, kind) == new


def test_bump_invalid() -> None:
    with pytest.raises(ValueError):
        bump_version("not-semver", "patch")
    with pytest.raises(ValueError):
        bump_version("1.2.3", "huge")


def test_set_version_in_text() -> None:
    text = '__version__ = "1.2.3"\nother = 1\n'
    out, n = set_version_in_text(text, "1.2.4")
    assert n == 1 and '__version__ = "1.2.4"' in out


def test_set_version_pyproject() -> None:
    text = '[project]\nname = "x"\nversion = "0.1.0"\n'
    out, n = set_version_in_text(text, "0.2.0")
    assert n == 1 and 'version = "0.2.0"' in out


def test_find_and_current_version(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('__version__ = "3.4.5"\n', encoding="utf-8")
    files = find_version_files(tmp_path)
    assert any(f.name == "__init__.py" for f in files)
    assert current_version(files) == "3.4.5"


# ---- docstring ----

def test_undocumented_detection() -> None:
    src = (
        'def documented():\n    """has doc."""\n    pass\n'
        'def bare():\n    pass\n'
        'class Foo:\n    """doc."""\n    def method(self):\n        pass\n'
        'def _private():\n    pass\n'
    )
    gaps = undocumented_in_source(src, "m.py")
    names = {g.symbol for g in gaps}
    assert "bare" in names and "Foo.method" in names
    assert "documented" not in names and "_private" not in names


def test_undocumented_syntax_error_safe() -> None:
    assert undocumented_in_source("def (((", "x.py") == []


def test_find_doc_gaps_skips_tests(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def bare():\n    pass\n", encoding="utf-8")
    (tmp_path / "test_m.py").write_text("def also_bare():\n    pass\n", encoding="utf-8")
    files = {g.file for g in find_doc_gaps(tmp_path)}
    assert "m.py" in files and "test_m.py" not in files


def test_doc_to_tasks_groups_by_file() -> None:
    from ronin_cli.docstring import DocGap
    tasks = to_tasks([DocGap("a", "function", "f.py", 1), DocGap("b", "function", "f.py", 5)])
    assert len(tasks) == 1                 # both in one file → one task
    assert "a" in tasks[0].detail and "b" in tasks[0].detail
