"""Tests for ``scripts/check_test_imports.py`` — the offline-tests pre-commit gate.

Two of these pin bugs the hook actually had on its first run rather than hypotheticals:

* :func:`test_a_docstring_mentioning_httpx_is_not_an_import` — a grep-based version of
  this hook failed on ``tests/telemetry/test_ronin_telemetry_transport.py``, a correct
  file whose docstring names the hazard it asserts against.
* :func:`test_the_eval_fixture_tree_is_excluded` — the first standalone walk descended
  into ``tests/evals/``, reporting SyntaxErrors from a CSV-quoting *fixture*: another
  project's code, deliberately broken, and none of this hook's business.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_test_imports import (  # noqa: E402  (path juggling must precede the import)
    EXCLUDED,
    check_file,
    imported_names,
    is_excluded,
    main,
    offending_module,
)


def _tree(source: str) -> ast.AST:
    return ast.parse(source)


@pytest.mark.parametrize(
    ("dotted", "expected"),
    [
        ("httpx", "httpx"),
        ("httpx.AsyncClient", "httpx"),
        ("requests", "requests"),
        ("http.client", "http.client"),
        ("urllib.request", "urllib.request"),
        # The precision that matters: parse is string work, request opens a socket.
        ("urllib.parse", None),
        ("http.cookies", None),
        ("json", None),
        ("pathlib", None),
        # A prefix match must respect the dot — `socketry` is not `socket`.
        ("socketry", None),
    ],
)
def test_offending_module_matches_on_dotted_prefixes(dotted: str, expected: str | None) -> None:
    assert offending_module(dotted) == expected


def test_a_nested_lazy_import_is_found() -> None:
    """The case the hook exists for: module-scope-only checks miss this entirely."""
    source = "def helper():\n    import httpx\n    return httpx\n"
    found = dict(imported_names(_tree(source)))
    assert "httpx" in found
    assert found["httpx"] == 2


def test_relative_imports_are_ignored() -> None:
    """``from . import x`` has no module name and cannot be a network library."""
    assert list(imported_names(_tree("from . import helpers\n"))) == []


def test_from_imports_report_the_module_not_the_symbol() -> None:
    assert list(imported_names(_tree("from httpx import AsyncClient\n"))) == [("httpx", 1)]


def test_a_docstring_mentioning_httpx_is_not_an_import(tmp_path: Path) -> None:
    """Regression: the repo's own transport test names ``import httpx`` in prose."""
    path = tmp_path / "test_prose.py"
    path.write_text('"""Asserts no lazy ``import httpx`` hides in a helper."""\n', encoding="utf-8")
    assert check_file(path) == []


def test_a_real_violation_is_reported_with_its_line(tmp_path: Path) -> None:
    path = tmp_path / "test_bad.py"
    path.write_text("import json\nimport httpx\n", encoding="utf-8")
    messages = check_file(path)
    assert len(messages) == 1
    assert ":2:" in messages[0]
    assert "httpx" in messages[0]


def test_an_unparseable_file_is_skipped_rather_than_crashing(tmp_path: Path) -> None:
    """ruff and pytest both report a syntax error better than this hook would."""
    path = tmp_path / "test_broken.py"
    path.write_text("def oops(:\n", encoding="utf-8")
    assert check_file(path) == []


def test_the_eval_fixture_tree_is_excluded() -> None:
    """Regression: fixtures are another project's code, and some do not parse."""
    assert is_excluded(REPO_ROOT / "tests" / "evals" / "single-file" / "x" / "fixture" / "a.py")
    assert not is_excluded(REPO_ROOT / "tests" / "tools" / "test_shell.py")


def test_excluded_is_not_empty() -> None:
    """A silently emptied EXCLUDED would make the exclusion tests vacuous."""
    assert EXCLUDED


def test_main_returns_zero_on_a_clean_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "test_ok.py"
    path.write_text("import json\n", encoding="utf-8")
    assert main([str(path)]) == 0
    assert capsys.readouterr().out == ""


def test_main_returns_one_and_explains_on_a_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "test_bad.py"
    path.write_text("import requests\n", encoding="utf-8")
    assert main([str(path)]) == 1
    out = capsys.readouterr().out
    assert "offline" in out
    assert "requests" in out


def test_non_python_paths_are_ignored(tmp_path: Path) -> None:
    """pre-commit can pass anything staged; only .py files are parseable."""
    path = tmp_path / "notes.md"
    path.write_text("import httpx\n", encoding="utf-8")
    assert main([str(path)]) == 0


def test_the_real_test_tree_is_clean() -> None:
    """The integration test: the suite as committed obeys the rule it enforces.

    If this ever fails, the fix is to mock the provider — not to widen the denylist.
    """
    assert main([]) == 0
