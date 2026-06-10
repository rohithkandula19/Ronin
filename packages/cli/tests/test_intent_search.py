"""Tests for intent_search — tokenizing, scoring, ranking."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.intent_search import (
    CodeUnit,
    extract_units,
    score_unit,
    search,
    search_prompt,
    tokenize,
)


def test_tokenize_drops_stopwords_and_stems() -> None:
    toks = tokenize("Where do we handle retries?")
    assert "where" not in toks and "do" not in toks and "we" not in toks
    assert "handl" in toks and "retr" in toks      # stemmed (retries → retr)


def test_extract_units_name_doc_body(tmp_path: Path) -> None:
    src = (
        'def retry_request(n):\n'
        '    """Retry an HTTP call with backoff."""\n'
        '    for i in range(n):\n'
        '        send()\n'
    )
    units = extract_units(src, "m.py")
    assert len(units) == 1
    u = units[0]
    assert "retr" in u.name_tokens and "request" in u.name_tokens
    assert "backoff" in u.doc_tokens
    assert "send" in u.body_tokens


def test_score_name_beats_body() -> None:
    name_hit = CodeUnit("retry", "function", "a.py", 1, name_tokens={"retr"})
    body_hit = CodeUnit("foo", "function", "b.py", 1, body_tokens={"retr"})
    terms = tokenize("retry")
    assert score_unit(terms, name_hit) > score_unit(terms, body_hit)


def test_score_zero_when_no_overlap() -> None:
    u = CodeUnit("foo", "function", "a.py", 1, name_tokens={"foo"})
    assert score_unit(tokenize("database migration"), u) == 0.0


def test_search_ranks_relevant_first(tmp_path: Path) -> None:
    (tmp_path / "net.py").write_text(
        'def retry_with_backoff():\n    """retry a failed request."""\n    pass\n',
        encoding="utf-8")
    (tmp_path / "math_util.py").write_text(
        'def add(a, b):\n    """sum two numbers."""\n    return a + b\n',
        encoding="utf-8")
    hits = search(tmp_path, "how do we retry requests")
    assert hits, "expected at least one hit"
    top_unit, _ = hits[0]
    assert top_unit.name == "retry_with_backoff"


def test_search_prompt_lists_candidates() -> None:
    u = CodeUnit("retry", "function", "net.py", 7)
    p = search_prompt("how do we retry", [(u, 6.0)])
    assert "net.py:7" in p and "retry" in p and "how do we retry" in p
