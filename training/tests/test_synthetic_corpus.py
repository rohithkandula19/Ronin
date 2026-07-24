"""Synthetic corpus generator: deterministic, registry-valid, D1/D2-closing."""
from __future__ import annotations

import json

from ronin_training.synthetic_corpus import generate, write_corpus
from ronin_training.validators import load_registry, validate_example


def test_deterministic_for_a_seed():
    a = generate(40, seed=7)
    b = generate(40, seed=7)
    assert a == b


def test_rows_valid_and_close_d1_d2():
    rows = generate(40, seed=7)
    n_reg = len(load_registry())
    for r in rows:
        assert validate_example(r) == []
        assert len(r["tools"]) == n_reg          # D1: full registry on every row
        for m in r["messages"]:
            for c in m.get("tool_calls") or []:
                assert isinstance(c["function"]["arguments"], dict)  # D2: objects


def test_split_is_hash_pinned(tmp_path):
    rows = generate(60, seed=7)
    counts = write_corpus(rows, tmp_path, seed=7)
    assert counts["train"] + counts["valid"] + counts["test"] == 60
    pin = (tmp_path / "test.jsonl.sha256").read_text().strip()
    assert pin == counts["test_sha256"] and len(pin) == 64


def test_refusal_family_calls_no_tools():
    rows = [r for r in generate(60, seed=7) if r["family"] == "refuse_destructive"]
    assert rows, "refusal family must be represented"
    for r in rows:
        assert all(not m.get("tool_calls") for m in r["messages"])
