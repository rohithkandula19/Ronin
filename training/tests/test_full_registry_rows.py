"""D1 regression: exported training rows carry the runtime's full tool registry,
and the frozen synthetic test split stays hash-pinned and separate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ronin_training.dataset_builder import build
from ronin_training.synthetic_corpus import generate
from ronin_training.validators import load_registry

VOLUMES = Path(__file__).resolve().parents[2] / "docs" / "volumes"
REPO = Path(__file__).resolve().parents[2]


def test_volume_rows_export_full_registry(tmp_path):
    build(str(VOLUMES), str(tmp_path / "out"))
    n_reg = len(load_registry())
    rows = [json.loads(l)
            for split in ("train", "valid", "test")
            for l in (tmp_path / "out" / f"{split}.jsonl").read_text().splitlines()]
    assert rows and all(len(r.get("tools") or []) == n_reg for r in rows)


def test_case_tools_mode_preserved_for_comparison(tmp_path):
    build(str(VOLUMES), str(tmp_path / "out"), full_registry=False)
    rows = [json.loads(l)
            for l in (tmp_path / "out" / "train.jsonl").read_text().splitlines()]
    # pre-D1 behavior on demand: rows keep their own case tools (0-6)
    assert any(len(r.get("tools") or []) < len(load_registry()) for r in rows)


def test_called_tool_count_spans_a_realistic_range(tmp_path):
    build(str(VOLUMES), str(tmp_path / "out"))
    counts = set()
    for split in ("train", "valid", "test"):
        for l in (tmp_path / "out" / f"{split}.jsonl").read_text().splitlines():
            r = json.loads(l)
            counts.add(sum(len(m.get("tool_calls") or []) for m in r["messages"]))
    # rows range from zero-call (refusals/prose) through multi-call sessions
    assert 0 in counts and max(counts) >= 4, counts


def test_frozen_synthetic_split_still_pinned_and_untouched():
    pin = REPO / "training" / "data" / "synthetic" / "test.jsonl.sha256"
    assert pin.is_file(), "frozen-split pin must stay in git"
    data = REPO / "training" / "data" / "synthetic" / "test.jsonl"
    if data.is_file():  # bulk data is gitignored; verify when present
        digest = hashlib.sha256(data.read_bytes()).hexdigest()
        assert digest == pin.read_text().strip()


def test_synthetic_rows_still_carry_full_registry():
    n_reg = len(load_registry())
    assert all(len(r["tools"]) == n_reg for r in generate(30, seed=7))
