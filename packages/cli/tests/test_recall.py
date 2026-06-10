"""Tests for recall — searching past sessions."""
from __future__ import annotations

from ronin_cli.recall import best_snippet, rank, score_text, tokenize


def test_tokenize_dedup_lower() -> None:
    assert tokenize("Fix the Auth auth BUG") == ["fix", "the", "auth", "bug"]
    assert tokenize("") == []


def test_score_text_counts_all_terms() -> None:
    assert score_text("auth auth login", ["auth", "login"]) == 3
    assert score_text("nothing here", ["auth"]) == 0


def test_best_snippet_picks_richest_entry() -> None:
    transcript = ["USER: hello", "ASSISTANT: I fixed the auth auth bug in login.py"]
    snip = best_snippet(transcript, ["auth", "bug"])
    assert "auth" in snip and "bug" in snip


def test_best_snippet_empty_when_no_match() -> None:
    assert best_snippet(["USER: hi", "ASSISTANT: yo"], ["auth"]) == ""


def test_rank_orders_and_filters() -> None:
    items = [
        ({"id": "s1", "title": "auth work"}, ["USER: fix auth", "ASSISTANT: fixed auth bug"]),
        ({"id": "s2", "title": "ui"}, ["USER: change color", "ASSISTANT: done"]),
        ({"id": "s3", "title": "more auth"}, ["USER: auth auth auth"]),
    ]
    hits = rank(items, "auth", limit=8)
    ids = [h.session_id for h in hits]
    assert "s2" not in ids               # zero-score dropped
    assert ids[0] == "s3"                # most occurrences ranks first
    assert hits[0].score == 3


def test_rank_empty_query() -> None:
    assert rank([({"id": "s1"}, ["USER: x"])], "") == []
