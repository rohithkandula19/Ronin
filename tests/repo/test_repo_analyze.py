"""The repo-intelligence engine, over a real (tiny) repository.

Rather than hand-build a ``RepoScan``, these tests write a handful of files to
``tmp_path`` and run the *real* :func:`scan_repo` on them — so the import resolution,
the pagerank, and the leaf detection are all exercised end to end, offline, in
milliseconds. The fixture repo is deliberately shaped to hit every branch: a hub module
that others import, an entry point, a genuine orphan, a test file, a package ``__init__``,
and a file with a syntax error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ronin.repo import deadcode, explain, health, overview, scan_repo
from ronin.repo.analyze import UnknownPathError


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        "class Core:\n    pass\n\n\ndef helper(x):\n    return x\n", encoding="utf-8"
    )
    (pkg / "app.py").write_text(
        "from pkg.core import Core\n\n\ndef main():\n    return Core()\n", encoding="utf-8"
    )
    (pkg / "__main__.py").write_text("from pkg.app import main\n\nmain()\n", encoding="utf-8")
    (pkg / "orphan.py").write_text("def unused():\n    return 1\n", encoding="utf-8")
    (pkg / "broken.py").write_text("def oops(:\n    pass\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_core.py").write_text(
        "from pkg.core import Core\n\n\ndef test_core():\n    assert Core()\n", encoding="utf-8"
    )
    return tmp_path


def test_overview_counts_languages_entry_points_and_orphans(repo: Path) -> None:
    o = overview(scan_repo(repo))
    assert o.total_files == 7  # 6 under pkg/ + 1 test
    assert o.files_by_language == {"Python": 7}
    assert "pkg/app.py" in o.entry_points  # defines main()
    assert "pkg/__main__.py" in o.entry_points  # runnable module
    assert o.orphan_count >= 1
    assert o.parse_error_count == 1  # broken.py
    # core.py is imported by app.py and the test, so it ranks above the orphan.
    top_paths = [m.path for m in o.top_modules]
    assert top_paths.index("pkg/core.py") < top_paths.index("pkg/orphan.py")


def test_explain_reports_neighbours_and_tags(repo: Path) -> None:
    scan = scan_repo(repo)
    core = explain(scan, "pkg/core.py")
    assert "pkg/app.py" in core.imported_by  # the change-impact set is real
    assert "class Core" in core.definitions
    assert not core.is_entry_point and not core.is_test

    app = explain(scan, "pkg/app.py")
    assert "pkg/core.py" in app.imports
    assert app.is_entry_point  # defines main()

    test = explain(scan, "tests/test_core.py")
    assert test.is_test


def test_explain_rejects_an_unknown_path(repo: Path) -> None:
    with pytest.raises(UnknownPathError):
        explain(scan_repo(repo), "pkg/does_not_exist.py")


def test_health_flags_orphan_parse_error_and_keeps_tests_signal_off(repo: Path) -> None:
    report = health(scan_repo(repo))
    kinds = {(s.kind, s.path) for s in report.signals}
    assert ("orphan-module", "pkg/orphan.py") in kinds
    assert any(s.kind == "parse-error" and s.path == "pkg/broken.py" for s in report.signals)
    # A test file exists, so the repo-level "no-tests" warning must NOT fire.
    assert "no-tests" not in report.summary
    assert report.test_file_count == 1
    # Entry points and the package __init__ are leaves by design — never flagged as orphans.
    assert ("orphan-module", "pkg/app.py") not in kinds
    assert ("orphan-module", "pkg/__init__.py") not in kinds


def test_health_warns_when_no_tests_exist(tmp_path: Path) -> None:
    (tmp_path / "solo.py").write_text("x = 1\n", encoding="utf-8")
    report = health(scan_repo(tmp_path))
    assert "no-tests" in report.summary
    assert report.test_file_count == 0


def test_deadcode_lists_the_orphan_not_the_entry_point(repo: Path) -> None:
    result = deadcode(scan_repo(repo))
    assert "pkg/orphan.py" in result.candidates
    assert "pkg/app.py" not in result.candidates  # entry point
    assert "pkg/__init__.py" not in result.candidates  # package marker
    assert "tests/test_core.py" not in result.candidates  # test
    assert result.note  # the "candidates only" caveat is always present
