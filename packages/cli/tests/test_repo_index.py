"""Tests for repo_index — pure ranking + budget selection, plus the persistent,
incremental SQLite index. No network, no model; everything is deterministic."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.repo_index import (
    budget_select,
    build_index,
    index_db_path,
    index_stats,
    load_index,
    query_index,
    rank_files,
)


# --------------------------------------------------------------------------- #
# rank_files — pure BM25 over path + symbols + summary.
# --------------------------------------------------------------------------- #
def test_rank_files_matching_beats_nonmatching() -> None:
    """A file whose path/symbols/summary match the query ranks above one that
    matches nothing."""
    index = [
        {"path": "auth.py", "symbols": ["login", "token"], "summary": "auth"},
        {"path": "ui.py", "symbols": ["render"], "summary": "ui widgets"},
    ]
    ranked = rank_files("login token", index)
    assert ranked, "expected at least one ranked file"
    assert ranked[0][0] == "auth.py"
    # The non-matching file scores zero and is dropped entirely.
    assert "ui.py" not in [p for p, _ in ranked]


def test_rank_files_path_hit_outranks_body_hit() -> None:
    """A query term in the *path* (weighted) outranks the same term only in the
    body summary."""
    index = [
        {"path": "services/payment.py", "symbols": [], "summary": "misc helpers"},
        {"path": "misc.py", "symbols": [], "summary": "process a payment refund"},
    ]
    ranked = rank_files("payment", index)
    assert [p for p, _ in ranked][0] == "services/payment.py"


def test_rank_files_deterministic_and_empty_query() -> None:
    index = [
        {"path": "a.py", "symbols": ["alpha"], "summary": ""},
        {"path": "b.py", "symbols": ["beta"], "summary": ""},
    ]
    assert rank_files("alpha", index) == rank_files("alpha", index)  # stable
    assert rank_files("", index) == []                               # no terms
    assert rank_files("alpha", []) == []                             # no docs


# --------------------------------------------------------------------------- #
# budget_select — greedy, highest-ranked-first, respects the token budget.
# --------------------------------------------------------------------------- #
def test_budget_select_respects_budget_and_orders_by_rank() -> None:
    ranked = [("a.py", 9.0), ("b.py", 5.0), ("c.py", 2.0)]
    sizes = {"a.py": 100, "b.py": 100, "c.py": 100}

    # Budget fits exactly two files → the two highest-ranked, in rank order.
    picked = budget_select(ranked, token_budget=200, size_of=lambda p: sizes[p])
    assert picked == ["a.py", "b.py"]

    # Total cost is 300; 250 still only admits the first two (third would overflow).
    picked = budget_select(ranked, token_budget=250, size_of=lambda p: sizes[p])
    assert picked == ["a.py", "b.py"]

    # A generous budget admits everything, still in rank order.
    picked = budget_select(ranked, token_budget=1000, size_of=lambda p: sizes[p])
    assert picked == ["a.py", "b.py", "c.py"]


def test_budget_select_skips_oversized_but_keeps_cheaper_after() -> None:
    """An item too big to fit is skipped, yet a cheaper lower-ranked item after it
    is still selected — the budget is packed, not abandoned at the first overflow."""
    ranked = [("big.py", 9.0), ("small.py", 4.0)]
    sizes = {"big.py": 10_000, "small.py": 50}
    picked = budget_select(ranked, token_budget=100, size_of=lambda p: sizes[p])
    assert picked == ["small.py"]


def test_budget_select_zero_budget_selects_nothing() -> None:
    ranked = [("a.py", 1.0)]
    assert budget_select(ranked, token_budget=0, size_of=lambda p: 1) == []


# --------------------------------------------------------------------------- #
# Persistent + incremental SQLite index.
# --------------------------------------------------------------------------- #
def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "auth.py").write_text(
        '"""Authentication."""\n\n'
        "def login(user, password):\n"
        "    return verify(user, password)\n",
        encoding="utf-8")
    (tmp_path / "ui.py").write_text(
        "def render():\n    return '<div/>'\n", encoding="utf-8")
    # An ignored dir must NOT be indexed.
    vendored = tmp_path / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "lib.js").write_text("function noise() {}\n", encoding="utf-8")
    return tmp_path


def test_build_index_walks_and_ignores(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    db = index_db_path(root)
    n = build_index(root, db)
    assert n == 2                       # auth.py + ui.py, node_modules pruned
    assert db.exists()

    paths = {e["path"] for e in load_index(db)}
    assert paths == {"auth.py", "ui.py"}
    # Symbols were extracted and persisted.
    by_path = {e["path"]: e for e in load_index(db)}
    assert "login" in by_path["auth.py"]["symbols"]


def test_build_index_is_incremental(tmp_path: Path) -> None:
    """A second build re-reads only changed files; deletes vanished ones; adds new."""
    import os
    import time

    root = _make_repo(tmp_path)
    db = index_db_path(root)
    build_index(root, db)

    # Delete one file, add another, and modify a third with a newer mtime.
    (root / "ui.py").unlink()
    (root / "new.py").write_text("def fresh():\n    return 1\n", encoding="utf-8")
    auth = root / "auth.py"
    auth.write_text('"""Auth v2."""\n\ndef login(u):\n    return signin(u)\n',
                    encoding="utf-8")
    future = time.time() + 10
    os.utime(auth, (future, future))

    n = build_index(root, db)
    by_path = {e["path"]: e for e in load_index(db)}
    assert set(by_path) == {"auth.py", "new.py"}        # ui.py gone, new.py added
    assert n == 2
    assert "signin" in by_path["auth.py"]["summary"]     # auth.py was re-read


def test_query_index_ranks_and_budgets(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    db = index_db_path(root)
    build_index(root, db)

    picked = query_index("login password", db, token_budget=8000)
    assert picked and picked[0] == "auth.py"

    # A tiny budget bounds the result — at most the single highest-ranked file.
    tiny = query_index("login password", db, token_budget=1)
    assert len(tiny) <= 1


def test_index_stats_reports_counts(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    db = index_db_path(root)
    build_index(root, db)
    stats = index_stats(db)
    assert stats["files"] == 2
    assert any(lang == "python" for lang, _ in stats["languages"])
