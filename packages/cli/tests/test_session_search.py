"""Tests for session_search — local, PII-redacted searchable session memory.

Covers the pure core (tokenize, rank, redact_for_index) plus an end-to-end build
of a tiny temp index from in-memory fake transcripts and a recall() over it. No
network, no real ~/.ronin — everything runs against a tmp_path DB.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ronin_cli import session_search as ss


# --------------------------------------------------------------------------- #
# tokenize
# --------------------------------------------------------------------------- #

def test_tokenize_lowercases_and_drops_stopwords() -> None:
    toks = ss.tokenize("Add Retry to the HTTP Client")
    assert "retry" in toks and "http" in toks and "client" in toks
    assert "add" in toks
    # stop-words and single chars are dropped
    assert "the" not in toks
    assert "to" not in toks


def test_tokenize_empty() -> None:
    assert ss.tokenize("") == []
    assert ss.tokenize("   ") == []


def test_tokenize_keeps_term_frequency() -> None:
    # not deduped — TF matters for ranking
    assert ss.tokenize("auth auth auth").count("auth") == 3


# --------------------------------------------------------------------------- #
# rank — a matching doc must out-score a non-matching one
# --------------------------------------------------------------------------- #

def test_rank_matching_beats_nonmatching() -> None:
    q = "http retry"
    assert ss.rank(q, "add retry to the http client") > ss.rank(q, "make a sandwich")


def test_rank_nonmatching_is_zero() -> None:
    assert ss.rank("http retry", "make a sandwich") == 0.0


def test_rank_empty_query_is_zero() -> None:
    assert ss.rank("", "anything at all") == 0.0


def test_rank_more_query_coverage_scores_higher() -> None:
    # a doc covering BOTH query terms beats one covering only one
    both = ss.rank("auth token", "the auth token was refreshed")
    one = ss.rank("auth token", "the auth flow changed")
    assert both > one


# --------------------------------------------------------------------------- #
# redact_for_index — planted secrets/PII must be masked
# --------------------------------------------------------------------------- #

def test_redact_masks_short_sk_token() -> None:
    out = ss.redact_for_index("here is a key sk-abc123 to use")
    assert "sk-abc123" not in out
    assert "sk-" not in out


def test_redact_masks_email() -> None:
    out = ss.redact_for_index("ping me at ada@example.com tomorrow")
    assert "ada@example.com" not in out
    assert "@example.com" not in out


def test_redact_masks_long_keys_via_shared_helper() -> None:
    out = ss.redact_for_index("openai sk-" + "a" * 40 + " and stripe rk_live_" + "b" * 24)
    assert "sk-" not in out
    assert "rk_live_" not in out


def test_redact_masks_slack_token() -> None:
    out = ss.redact_for_index("token xoxb-12345-abc here")
    assert "xoxb-" not in out


def test_redact_clean_text_untouched() -> None:
    text = "just a normal note about the retry logic"
    assert ss.redact_for_index(text) == text


# --------------------------------------------------------------------------- #
# end-to-end: build a tiny temp index from fake transcripts, then recall()
# --------------------------------------------------------------------------- #

def _write_session(sdir: Path, sid: str, title: str, transcript: list[str],
                   when: str = "2026-06-18T10:00:00") -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": sid,
        "root": "/tmp/fake-repo",
        "proj": "deadbeef0000",
        "updated": when,
        "title": title,
        "turns": sum(1 for e in transcript if e.startswith("USER: ")),
        "transcript": transcript,
    }
    (sdir / f"{sid}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_index_and_recall_finds_right_session(tmp_path: Path) -> None:
    sdir = tmp_path / "sessions"
    db = tmp_path / "recall.db"

    _write_session(sdir, "20260618-100000-aaa", "http retry work", [
        "USER: please add a retry with backoff to the http client",
        "ASSISTANT: done — wrapped requests in an exponential backoff retry.",
    ])
    _write_session(sdir, "20260618-110000-bbb", "lunch chat", [
        "USER: what should I make for lunch",
        "ASSISTANT: how about a sandwich",
    ])

    n = ss.index_sessions(sdir, db)
    assert n == 2

    hits = ss.recall("http retry client", db, limit=8)
    assert hits, "expected at least one hit"
    # the http session must rank first
    assert hits[0]["session_id"] == "20260618-100000-aaa"
    # shape of a hit
    top = hits[0]
    assert set(top) == {"session_id", "when", "snippet", "score"}
    assert top["when"] == "2026-06-18T10:00:00"
    assert top["score"] > 0
    assert "retry" in top["snippet"].lower() or "http" in top["snippet"].lower()
    # the lunch session must NOT be the http match
    ids = [h["session_id"] for h in hits]
    assert "20260618-110000-bbb" not in ids or ids.index("20260618-110000-bbb") > 0


def test_recall_redacts_secrets_in_index(tmp_path: Path) -> None:
    """A secret planted in a transcript must not survive into the index/snippet."""
    sdir = tmp_path / "sessions"
    db = tmp_path / "recall.db"
    _write_session(sdir, "20260618-120000-ccc", "secret session", [
        "USER: store this api key sk-abc123 and ping ada@example.com about retry",
        "ASSISTANT: ok, noted the retry change",
    ])
    ss.index_sessions(sdir, db)

    # raw DB bytes must not contain the secret
    raw = db.read_bytes()
    assert b"sk-abc123" not in raw
    assert b"ada@example.com" not in raw

    hits = ss.recall("retry", db, limit=8)
    assert hits
    blob = " ".join(h["snippet"] for h in hits)
    assert "sk-abc123" not in blob
    assert "ada@example.com" not in blob


def test_recall_empty_query_returns_empty(tmp_path: Path) -> None:
    sdir = tmp_path / "sessions"
    db = tmp_path / "recall.db"
    _write_session(sdir, "s1", "t", ["USER: hi", "ASSISTANT: yo"])
    ss.index_sessions(sdir, db)
    assert ss.recall("", db) == []


def test_recall_missing_db_returns_empty(tmp_path: Path) -> None:
    assert ss.recall("anything", tmp_path / "does-not-exist.db") == []


def test_recall_no_match_returns_empty(tmp_path: Path) -> None:
    sdir = tmp_path / "sessions"
    db = tmp_path / "recall.db"
    _write_session(sdir, "s1", "auth work", ["USER: fix the auth bug"])
    ss.index_sessions(sdir, db)
    assert ss.recall("kubernetes helm chart", db) == []


def test_index_is_idempotent(tmp_path: Path) -> None:
    sdir = tmp_path / "sessions"
    db = tmp_path / "recall.db"
    _write_session(sdir, "s1", "retry work", ["USER: add retry to http client"])
    ss.index_sessions(sdir, db)
    ss.index_sessions(sdir, db)  # re-index — must not duplicate
    hits = ss.recall("retry http", db)
    assert len([h for h in hits if h["session_id"] == "s1"]) == 1


# --------------------------------------------------------------------------- #
# render_recall — smoke test (must not raise; emits teal/mute markup)
# --------------------------------------------------------------------------- #

def test_render_recall_smoke() -> None:
    from rich.console import Console
    import io

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=100)
    ss.render_recall(console, [
        {"session_id": "20260618-100000-aaa", "when": "2026-06-18T10:00:00",
         "snippet": "add retry to the http client", "score": 2.5},
    ])
    out = buf.getvalue()
    assert "20260618-10000" in out  # id rendered (truncated to 14 chars)
    assert "retry" in out


def test_render_recall_empty() -> None:
    from rich.console import Console
    import io

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=100)
    ss.render_recall(console, [])
    assert "no matching" in buf.getvalue().lower()


# --------------------------------------------------------------------------- #
# fallback ranker: force the plain-table path and confirm it still ranks
# --------------------------------------------------------------------------- #

def test_like_fallback_path_ranks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the non-FTS5 path and confirm index+recall still find the right one."""
    monkeypatch.setattr(ss, "_fts5_available", lambda conn: False)
    sdir = tmp_path / "sessions"
    db = tmp_path / "recall_like.db"
    _write_session(sdir, "s1", "http retry", ["USER: add retry to the http client"])
    _write_session(sdir, "s2", "lunch", ["USER: make a sandwich"])
    ss.index_sessions(sdir, db)
    hits = ss.recall("http retry client", db)
    assert hits and hits[0]["session_id"] == "s1"
