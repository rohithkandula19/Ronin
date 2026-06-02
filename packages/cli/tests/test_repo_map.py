"""Tests for the semantic repo map (BM25 code retrieval)."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.repo_map import (
    RepoIndex,
    _extract_symbols,
    _tokenize,
    repo_map,
)


def test_tokenizer_splits_snake_and_camel() -> None:
    toks = set(_tokenize("def loadUserConfig(): pass  # parse_auth_token"))
    # camelCase pieces + whole word
    assert {"load", "user", "config", "loaduserconfig"} <= toks
    # snake_case is already split by the word regex
    assert {"parse", "auth", "token"} <= toks


def test_extract_symbols_python_and_js() -> None:
    py = "class Foo:\n    def bar(self):\n        pass\n\ndef top_level():\n    pass\n"
    names = {n for n, _ in _extract_symbols(py)}
    assert {"Foo", "bar", "top_level"} <= names
    js = "export function makeWidget(x) {}\nexport const useThing = () => {}\n"
    jsnames = {n for n, _ in _extract_symbols(js)}
    assert {"makeWidget", "useThing"} <= jsnames


def test_ranks_relevant_file_first_and_ignores_vendor(tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text(
        "def authenticate_user(token):\n    return verify_token(token)\n", encoding="utf-8")
    (tmp_path / "math_utils.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "junk.py").write_text("def authenticate_user(): pass\n", encoding="utf-8")

    idx = RepoIndex(tmp_path).build()
    results = idx.search("authenticate the user token", k=5)
    assert results, "expected at least one hit"
    assert results[0][1].path == "auth.py"               # most relevant first
    assert all("node_modules" not in d.path for _, d in results)  # vendor ignored


def test_repo_map_tool_output_lists_file_and_symbols(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text(
        "def load_config(path):\n    return {}\n", encoding="utf-8")
    out = repo_map("load configuration from disk", root=tmp_path, k=3, refresh=True)
    assert "config.py" in out
    assert "load_config" in out          # symbol outline shown


def test_search_empty_query_or_no_match_is_safe(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
    idx = RepoIndex(tmp_path).build()
    assert idx.search("", k=5) == []
    assert idx.search("zzzznonexistenttokenxyz", k=5) == []
