"""Tests for two coding-tool robustness fixes:
1. list/search/glob ignore vendored dirs (venv, node_modules, …) so they don't
   drown the context in thousands of dependency files.
2. list/search/glob accept `path` as an alias for `directory` (models reach for
   it constantly; a bare TypeError just wastes a round-trip)."""
from __future__ import annotations

from ronin_cli.code_tools import build_code_tools


def _tools(root):
    return {t.name: t for t in build_code_tools(root)}


def _seed(root) -> None:
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    # vendored dirs that must be ignored
    for d in ("venv", "node_modules", ".venv", "dist", "__pycache__"):
        sub = root / d / "pkg"
        sub.mkdir(parents=True)
        (sub / "junk.py").write_text("y = 2\n", encoding="utf-8")


def test_list_files_ignores_vendored_dirs(tmp_path) -> None:
    _seed(tmp_path)
    out = _tools(tmp_path)["list_files"].handler(directory=".")
    assert "app.py" in out
    # nothing from venv / node_modules / dist / etc.
    assert not any("venv" in p or "node_modules" in p or "dist" in p or "__pycache__" in p
                   for p in out)


def test_search_files_ignores_vendored_dirs(tmp_path) -> None:
    _seed(tmp_path)
    # 'y = 2' only exists inside vendored dirs → should find nothing
    hits = _tools(tmp_path)["search_files"].handler(query="y = 2")
    assert hits == []
    # but a real source hit is found
    hits2 = _tools(tmp_path)["search_files"].handler(query="x = 1")
    assert any(h["file"] == "app.py" for h in hits2)


def test_list_files_accepts_path_alias(tmp_path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.py").write_text("z = 3\n", encoding="utf-8")
    # the model passing path= instead of directory= must NOT raise
    out = _tools(tmp_path)["list_files"].handler(path="sub")
    assert out == ["sub/f.py"]


def test_search_files_accepts_path_alias(tmp_path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.py").write_text("needle\n", encoding="utf-8")
    hits = _tools(tmp_path)["search_files"].handler(query="needle", path="sub")
    assert any(h["file"] == "sub/f.py" for h in hits)


def test_glob_accepts_path_alias(tmp_path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.py").write_text("\n", encoding="utf-8")
    out = _tools(tmp_path)["glob"].handler(pattern="*.py", path="sub")
    assert out == ["sub/a.py"]
