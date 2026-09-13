"""Docstring-gap detection for ``ronin1 dev docstring``.

The release-version tests that shared this file moved to
``tests/scripts/test_release_manifest.py`` with the module they covered. They were
only ever neighbours here: ``ronin_cli.docstring`` never depended on the release
maths, and release tooling is build tooling rather than part of a shipped CLI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli.docstring import find_doc_gaps, to_tasks, undocumented_in_source


# ---- docstring ----


def test_undocumented_detection() -> None:
    src = (
        'def documented():\n    """has doc."""\n    pass\n'
        "def bare():\n    pass\n"
        'class Foo:\n    """doc."""\n    def method(self):\n        pass\n'
        "def _private():\n    pass\n"
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
    assert len(tasks) == 1  # both in one file → one task
    assert "a" in tasks[0].detail and "b" in tasks[0].detail
