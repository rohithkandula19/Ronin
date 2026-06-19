"""Tests for the fully-local embedding backend (``ronin_cli.local_embed``).

These pin only the PURE, offline pieces — the deterministic hashing embedder,
cosine, and the backend-selection policy. No network is touched: the Ollama path
is never exercised here (we drive ``embed`` with ``prefer_local=False`` so it goes
straight to hashing, and we assert ``pick_backend`` never picks a remote backend).
"""
from __future__ import annotations

import math

import pytest

from ronin_cli.local_embed import (
    DEFAULT_DIM,
    cosine,
    embed,
    hashing_embed,
    integration_note,
    pick_backend,
)


# --------------------------------------------------------------------------
# hashing_embed — determinism
# --------------------------------------------------------------------------

def test_hashing_embed_is_deterministic() -> None:
    # Same text → identical vector, every call (required for the disk cache).
    a = hashing_embed("add retry to http client")
    b = hashing_embed("add retry to http client")
    assert a == b


def test_hashing_embed_deterministic_across_fresh_call() -> None:
    # Not relying on object identity / interning — recompute from a new literal.
    text = "verify credentials before issuing a session token"
    assert hashing_embed(text) == hashing_embed("verify credentials before "
                                                 "issuing a session token")


def test_hashing_embed_respects_dim() -> None:
    assert len(hashing_embed("hello world", dim=128)) == 128
    assert len(hashing_embed("hello world", dim=512)) == 512


def test_hashing_embed_default_dim() -> None:
    assert len(hashing_embed("anything")) == DEFAULT_DIM


def test_different_texts_give_different_vectors() -> None:
    assert hashing_embed("authenticate the user") != hashing_embed("bake a cake")


# --------------------------------------------------------------------------
# hashing_embed — normalization
# --------------------------------------------------------------------------

def test_hashing_embed_is_l2_normalized() -> None:
    vec = hashing_embed("retry the failed http request")
    norm = math.sqrt(sum(x * x for x in vec))
    assert norm == pytest.approx(1.0, abs=1e-9)


def test_hashing_embed_normalized_for_many_inputs() -> None:
    for text in ["a", "the quick brown fox", "CamelCaseSymbol(arg1, arg2)",
                 "snake_case_name = 42", "émojis and ünïcode"]:
        vec = hashing_embed(text)
        norm = math.sqrt(sum(x * x for x in vec))
        assert norm == pytest.approx(1.0, abs=1e-9)


def test_hashing_embed_empty_text_is_zero_vector() -> None:
    # No features → all-zeros (can't be normalized); must not raise.
    vec = hashing_embed("")
    assert len(vec) == DEFAULT_DIM
    assert all(x == 0.0 for x in vec)


def test_hashing_embed_nonnegative() -> None:
    # Count-based features are non-negative; normalization preserves the sign.
    assert all(x >= 0.0 for x in hashing_embed("some mixed Text 123"))


# --------------------------------------------------------------------------
# cosine
# --------------------------------------------------------------------------

def test_cosine_identical_is_one() -> None:
    v = hashing_embed("idempotent retry with exponential backoff")
    assert cosine(v, v) == pytest.approx(1.0, abs=1e-9)


def test_cosine_self_normalized_vectors() -> None:
    # Plain orthonormal check on hand-built vectors.
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector_is_zero_not_error() -> None:
    assert cosine([0.0, 0.0, 0.0], [1.0, 2.0, 3.0]) == 0.0
    assert cosine([0.0], [0.0]) == 0.0


def test_cosine_mismatched_length_is_zero() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_cosine_in_unit_range() -> None:
    a = hashing_embed("parse the json response body")
    b = hashing_embed("render the html template")
    assert -1.0 - 1e-9 <= cosine(a, b) <= 1.0 + 1e-9


# --------------------------------------------------------------------------
# semantic-ish: similar > dissimilar (the core property)
# --------------------------------------------------------------------------

def test_similar_scores_higher_than_dissimilar() -> None:
    # The headline guarantee from the brief: a near-reworded doc must score
    # strictly higher than an unrelated one.
    a = hashing_embed("add retry to http client")
    b = hashing_embed("add retry to the http client")   # near-duplicate
    c = hashing_embed("bake a cake")                     # unrelated
    assert cosine(a, b) > cosine(a, c)


def test_query_closer_to_relevant_doc_on_overlap() -> None:
    # Simple overlap case: a query ranks its relevant doc above an irrelevant one.
    query = hashing_embed("how do we authenticate users")
    relevant = hashing_embed("function to authenticate users and check password")
    irrelevant = hashing_embed("compute fibonacci numbers recursively")
    assert cosine(query, relevant) > cosine(query, irrelevant)


def test_shared_word_beats_no_shared_word() -> None:
    base = hashing_embed("database connection pool")
    shares = hashing_embed("the database is slow")          # shares 'database'
    none = hashing_embed("orange juice tastes sweet")       # shares nothing
    assert cosine(base, shares) > cosine(base, none)


def test_ranking_picks_relevant_doc() -> None:
    query = hashing_embed("retry failed network request")
    docs = {
        "retry.py": hashing_embed("retry a failed network request with backoff"),
        "cake.py": hashing_embed("recipe for chocolate cake"),
        "colors.py": hashing_embed("list of rainbow colors"),
    }
    ranked = sorted(docs.items(), key=lambda kv: cosine(query, kv[1]), reverse=True)
    assert ranked[0][0] == "retry.py"


# --------------------------------------------------------------------------
# pick_backend — pure policy, never remote
# --------------------------------------------------------------------------

def test_pick_backend_offline_with_ollama_is_ollama() -> None:
    assert pick_backend(offline=True, ollama_ok=True) == "ollama"


def test_pick_backend_offline_without_ollama_is_hashing() -> None:
    assert pick_backend(offline=True, ollama_ok=False) == "hashing"


def test_pick_backend_offline_is_always_local_never_remote() -> None:
    # Whatever the Ollama state, offline must resolve to a LOCAL backend only.
    for ollama_ok in (True, False):
        choice = pick_backend(offline=True, ollama_ok=ollama_ok)
        assert choice in ("ollama", "hashing")           # never a remote API


def test_pick_backend_online_still_local_only() -> None:
    # Even when not offline, this picker only ever returns local backends.
    assert pick_backend(offline=False, ollama_ok=True) == "ollama"
    assert pick_backend(offline=False, ollama_ok=False) == "hashing"


# --------------------------------------------------------------------------
# embed — offline path (prefer_local=False skips the Ollama probe entirely)
# --------------------------------------------------------------------------

def test_embed_offline_uses_hashing_and_matches_hashing_embed() -> None:
    texts = ["add retry to http client", "bake a cake"]
    out = embed(texts, prefer_local=False)
    assert out == [hashing_embed(t) for t in texts]


def test_embed_empty_input() -> None:
    assert embed([], prefer_local=False) == []


def test_embed_preserves_order_and_count() -> None:
    texts = ["one", "two", "three"]
    out = embed(texts, prefer_local=False)
    assert len(out) == 3
    assert out[0] == hashing_embed("one")
    assert out[2] == hashing_embed("three")


def test_embed_offline_similar_beats_dissimilar() -> None:
    a, b, c = embed(
        ["add retry to http client",
         "add retry to the http client",
         "bake a cake"],
        prefer_local=False,
    )
    assert cosine(a, b) > cosine(a, c)


# --------------------------------------------------------------------------
# integration note
# --------------------------------------------------------------------------

def test_integration_note_is_descriptive_text() -> None:
    note = integration_note()
    assert isinstance(note, str)
    assert "get_backend" in note
    assert "offline" in note.lower()
