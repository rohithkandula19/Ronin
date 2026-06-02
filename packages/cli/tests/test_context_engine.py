"""Tests for automatic context engineering — relevance-ranked file injection."""
from __future__ import annotations

import time

from ronin_cli import context_engine
from ronin_cli.repo_map import _CACHE


def _seed_repo(root) -> None:
    (root / "auth.py").write_text(
        "def login(user, password):\n    return verify_token(user)\n\n"
        "def verify_token(user):\n    return True\n", encoding="utf-8")
    (root / "models.py").write_text(
        "class User:\n    pass\n\nclass Session:\n    pass\n", encoding="utf-8")
    (root / "unrelated.py").write_text("def quux():\n    return 42\n", encoding="utf-8")


def _clear_cache() -> None:
    _CACHE.clear()


def test_relevant_context_returns_block_when_built(tmp_path) -> None:
    _clear_cache()
    _seed_repo(tmp_path)
    # build_sync so we don't depend on background timing
    block = context_engine.relevant_context("how does login work", tmp_path, build_sync=True)
    assert block is not None
    assert "auth.py" in block
    assert "login" in block            # symbol outline included
    assert "Relevant code" in block


def test_relevant_context_nonblocking_first_call_returns_none(tmp_path) -> None:
    _clear_cache()
    _seed_repo(tmp_path)
    # cold index → background build kicked off, this turn injects nothing
    assert context_engine.relevant_context("login", tmp_path) is None
    # the background build eventually populates the cache
    for _ in range(50):
        if context_engine.cached_index(tmp_path) is not None:
            break
        time.sleep(0.02)
    assert context_engine.cached_index(tmp_path) is not None
    # now a warm call returns a block
    assert context_engine.relevant_context("login", tmp_path) is not None


def test_short_query_skipped(tmp_path) -> None:
    _clear_cache()
    _seed_repo(tmp_path)
    assert context_engine.relevant_context("hi", tmp_path, build_sync=True) is None


def test_no_match_returns_none(tmp_path) -> None:
    _clear_cache()
    _seed_repo(tmp_path)
    block = context_engine.relevant_context("zzzzz nonexistent concept", tmp_path, build_sync=True)
    assert block is None


def test_format_context_block_thresholds_and_caps() -> None:
    class _Doc:
        def __init__(self, path, symbols):
            self.path = path
            self.symbols = symbols

    results = [
        (10.0, _Doc("a.py", [("foo", 1), ("bar", 2)])),
        (9.0, _Doc("b.py", [("baz", 1)])),
        (1.0, _Doc("weak.py", [])),   # below 0.35*top → dropped
    ]
    block = context_engine.format_context_block(results, max_files=5)
    assert "a.py — foo, bar" in block
    assert "b.py" in block
    assert "weak.py" not in block      # weak incidental match excluded


def test_format_context_block_empty() -> None:
    assert context_engine.format_context_block([]) is None
