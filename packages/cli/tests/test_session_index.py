"""Tests for session search — the SQLite FTS index over past sessions.

Fast and offline: no provider, no network. We archive a couple of synthetic
sessions, index them, and assert the search really finds the right ones with
snippets. Both the FTS path and the pure-ranker fallback are covered.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli import session_index, sessions
from ronin_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # sessions live under ./.ronin (cwd-relative); the index db lives under
    # ~/.ronin (HOME). Isolate both so tests never touch the real machine.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions._session_id = None
    yield


def _seed_two_sessions(root: Path) -> None:
    sessions.save_session(
        root,
        ["USER: the login flow is broken", "ASSISTANT: I fixed the auth bug in login.py"],
        session_id="20260101-000001-aaaaaa",
    )
    sessions.save_session(
        root,
        ["USER: change the button color", "ASSISTANT: updated the CSS to teal"],
        session_id="20260101-000002-bbbbbb",
    )


def test_fts5_is_available_in_this_build() -> None:
    # The whole point of the indexed path; if this build lacked FTS5 we'd be on
    # the fallback. CI's stdlib sqlite ships FTS5, so assert we get the fast path.
    assert session_index.fts5_available() is True


def test_search_finds_the_right_session_with_snippet(tmp_path: Path) -> None:
    _seed_two_sessions(tmp_path / "repo")
    hits = session_index.search("auth bug")
    assert hits, "expected a match for 'auth bug'"
    assert hits[0].session_id == "20260101-000001-aaaaaa"
    assert "auth" in hits[0].snippet.lower()
    # the unrelated session is not returned for this query
    assert all(h.session_id != "20260101-000002-bbbbbb" for h in hits)


def test_search_other_term_hits_other_session(tmp_path: Path) -> None:
    _seed_two_sessions(tmp_path / "repo")
    hits = session_index.search("teal color")
    assert hits and hits[0].session_id == "20260101-000002-bbbbbb"


def test_search_empty_query_and_no_match(tmp_path: Path) -> None:
    _seed_two_sessions(tmp_path / "repo")
    assert session_index.search("") == []
    assert session_index.search("nonexistentterm") == []


def test_reindex_is_incremental(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _seed_two_sessions(root)
    first = session_index.reindex()
    assert first == 2                      # both written
    second = session_index.reindex()
    assert second == 0                     # nothing changed -> nothing rewritten

    # change a session -> only it is re-indexed
    sessions.save_session(root, ["USER: now about deployments", "ASSISTANT: use fly.io"],
                          session_id="20260101-000001-aaaaaa")
    assert session_index.reindex() == 1


def test_search_scoped_to_repo_with_here(tmp_path: Path) -> None:
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    sessions.save_session(a, ["USER: auth bug here", "ASSISTANT: fixed in a"],
                          session_id="20260101-000001-aaaaaa")
    sessions.save_session(b, ["USER: auth bug there", "ASSISTANT: fixed in b"],
                          session_id="20260101-000002-bbbbbb")
    # scoped to repo a: only a's session
    hits = session_index.search("auth bug", root=a)
    ids = {h.session_id for h in hits}
    assert ids == {"20260101-000001-aaaaaa"}
    # unscoped: both
    all_ids = {h.session_id for h in session_index.search("auth bug")}
    assert all_ids == {"20260101-000001-aaaaaa", "20260101-000002-bbbbbb"}


def test_fallback_used_when_fts5_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_two_sessions(tmp_path / "repo")
    monkeypatch.setattr(session_index, "fts5_available", lambda: False)
    hits = session_index.search("auth bug")
    assert hits and hits[0].session_id == "20260101-000001-aaaaaa"  # ranker still finds it


def test_punctuation_query_does_not_crash_fts(tmp_path: Path) -> None:
    # FTS5 MATCH has its own operators; a query full of them must be quoted, not
    # injected. This should return cleanly (likely no hits), never raise.
    _seed_two_sessions(tmp_path / "repo")
    assert isinstance(session_index.search('"auth" OR (login*)'), list)


# ---------- CLI ----------

def test_cli_search_and_reindex(tmp_path: Path) -> None:
    _seed_two_sessions(tmp_path / "repo")

    r = runner.invoke(app, ["search", "auth", "bug"])
    assert r.exit_code == 0
    assert "20260101-00000" in r.stdout
    assert "auth" in r.stdout.lower()

    r = runner.invoke(app, ["search", "--reindex"])
    assert r.exit_code == 0 and "indexed" in r.stdout

    r = runner.invoke(app, ["search", "nothingmatchesthis"])
    assert r.exit_code == 0 and "no any sessions matched" in r.stdout

    r = runner.invoke(app, ["search"])
    assert r.exit_code == 2     # nothing to search for
