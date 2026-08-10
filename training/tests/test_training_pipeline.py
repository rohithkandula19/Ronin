"""Tests for the volume → JSONL pipeline.

These exercise the real volumes under `docs/volumes` plus synthetic edge cases.
The invariants they lock: malformed blocks are reported not dropped, the registry
check rejects unparseable tool calls, banned licenses are refused, the split is
deterministic and represents every family in train, and the shipped JSONL contains
only MLX-consumable keys.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from ronin_training.dataset_builder import build
from ronin_training.split_dataset import split_by_family
from ronin_training.validators import load_registry, validate_eval, validate_example
from ronin_training.volume_parser import parse_dir, parse_text

REPO = Path(__file__).resolve().parents[2]
VOLUMES = REPO / "docs" / "volumes"


# ---------------------------------------------------------------- parser

def test_parse_extracts_sft_and_eval_blocks():
    text = (
        "prose\n\n```ronin-sft\n"
        '{"id": "x_1", "family": "f", "source_volume": "T", "license": "project-owned",'
        ' "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]}\n'
        "```\n\n```ronin-eval\n"
        '{"eval_id": "e_1", "category": "grounding", "prompt": "p"}\n```\n'
    )
    res = parse_text(text, source="t.md")
    assert len(res.sft) == 1 and len(res.evals) == 1
    assert res.errors == []
    assert res.sft[0]["_source_file"] == "t.md"


def test_malformed_block_is_reported_not_dropped():
    res = parse_text("```ronin-sft\n{not json}\n```", source="bad.md")
    assert res.sft == []
    assert len(res.errors) == 1 and "invalid JSON" in res.errors[0]


def test_non_object_block_is_reported():
    res = parse_text('```ronin-sft\n[1, 2, 3]\n```', source="arr.md")
    assert res.sft == [] and "not a JSON object" in res.errors[0]


# ---------------------------------------------------------------- validators

def _row(**over):
    base = {
        "id": "valid_row_1", "family": "f", "source_volume": "T",
        "license": "project-owned",
        "messages": [
            {"role": "user", "content": "read it"},
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": {"path": "a.py"}}}
            ]},
        ],
    }
    base.update(over)
    return base


def test_valid_row_passes():
    assert validate_example(_row()) == []


def test_unknown_tool_rejected():
    bad = _row(messages=[
        {"role": "user", "content": "x"},
        {"role": "assistant", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "teleport", "arguments": {}}}
        ]},
    ])
    errs = validate_example(bad)
    assert any("unknown tool" in e for e in errs)


def test_bad_tool_arguments_rejected():
    bad = _row(messages=[
        {"role": "user", "content": "x"},
        {"role": "assistant", "tool_calls": [
            {"id": "c1", "type": "function",
             # read_file requires `path`; give it a wrong-typed/absent one
             "function": {"name": "read_file", "arguments": {"path": 123}}}
        ]},
    ])
    errs = validate_example(bad)
    assert any("schema" in e for e in errs)


def test_non_object_tool_arguments_rejected():
    # D2: the schema itself now forbids string-typed arguments — a "{not json}"
    # string (or ANY string) never reaches the JSON-decode path; it dies at the
    # schema gate.
    bad = _row(messages=[
        {"role": "user", "content": "x"},
        {"role": "assistant", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": "{not json}"}}
        ]},
    ])
    errs = validate_example(bad)
    assert errs and "schema" in errs[0]


def test_banned_license_rejected():
    assert any("proprietary" in e for e in validate_example(_row(license="project-owned"))) is False
    # license enum forbids the banned strings at the schema layer:
    errs = validate_example(_row(license="claude_output"))
    assert errs  # rejected (schema enum), never accepted as an SFT target


def test_internal_source_file_key_does_not_break_validation():
    row = _row()
    row["_source_file"] = "somewhere.md"
    assert validate_example(row) == []


def test_eval_unknown_tool_reference_rejected():
    ec = {"eval_id": "e_1", "category": "gate_respect", "prompt": "p",
          "must_not_call_tools": ["make_coffee"]}
    assert any("unknown tool" in e for e in validate_eval(ec))


def test_registry_has_expected_gated_tools():
    reg = load_registry()
    for t in ("edit_file", "multi_edit", "write_file", "run_command", "run_background"):
        assert reg[t]["gated"] is True


# ---------------------------------------------------------------- split

def _rows(family, n):
    return [{"id": f"{family}_{i:03d}", "family": family} for i in range(n)]


def test_split_is_deterministic():
    rows = _rows("a", 12) + _rows("b", 8) + _rows("c", 5)
    assert split_by_family(rows) == split_by_family(rows)


def test_split_no_row_spans_two_splits():
    rows = _rows("a", 12) + _rows("b", 8)
    tr, va, te = split_by_family(rows)
    ids = [r["id"] for r in tr + va + te]
    assert len(ids) == len(set(ids)) == len(rows)


def test_every_family_is_in_train():
    rows = _rows("a", 3) + _rows("b", 3) + _rows("c", 1)
    tr, va, te = split_by_family(rows)
    fams_in_train = {r["family"] for r in tr}
    assert fams_in_train == {"a", "b", "c"}


def test_singletons_go_to_train():
    tr, va, te = split_by_family(_rows("solo", 1))
    assert len(tr) == 1 and va == [] and te == []


def test_ratio_is_near_target_on_a_larger_corpus():
    rows = _rows("a", 100)
    tr, va, te = split_by_family(rows)
    # ~80/10/10 (first row reserved to train nudges train up slightly)
    assert 0.78 <= len(tr) / 100 <= 0.90
    assert len(va) >= 8 and len(te) >= 8


# ---------------------------------------------------------------- end-to-end

def test_build_real_volumes(tmp_path):
    out = tmp_path / "data" / "generated"
    report = build(VOLUMES, out, strict=True)
    assert report["rejected"] == 0
    assert report["unique"] >= 50               # spec Phase 1 acceptance
    assert report["evals"] >= 1
    for name in ("train.jsonl", "valid.jsonl", "test.jsonl"):
        rows = [json.loads(l) for l in (out / name).read_text().splitlines()]
        assert rows, f"{name} is empty"
        for r in rows:                          # only MLX-consumable keys ship
            assert set(r).issubset({"messages", "tools"})
    evals = (out.parent / "evals" / "ronin_protocol_eval.jsonl").read_text().splitlines()
    for line in evals:                          # no internal keys leak into evals
        assert not any(k.startswith("_") for k in json.loads(line))


def test_build_is_strict_by_default(tmp_path):
    bad = tmp_path / "volumes"
    bad.mkdir()
    (bad / "volume_bad.md").write_text(
        '```ronin-sft\n'
        '{"id": "bad_1", "family": "f", "source_volume": "T", "license": "project-owned",'
        ' "messages": [{"role": "user", "content": "x"},'
        ' {"role": "assistant", "tool_calls": [{"id": "c", "type": "function",'
        ' "function": {"name": "nonexistent_tool", "arguments": {}}}]}]}\n```\n'
    )
    with pytest.raises(ValueError):
        build(bad, tmp_path / "out", strict=True)


def test_build_allow_invalid_excludes_bad_rows(tmp_path):
    bad = tmp_path / "volumes"
    bad.mkdir()
    (bad / "volume_mix.md").write_text(
        # one good row, one referencing an unknown tool
        '```ronin-sft\n{"id": "good_1", "family": "f", "source_volume": "T",'
        ' "license": "project-owned", "messages": [{"role": "user", "content": "x"},'
        ' {"role": "assistant", "content": "ok"}]}\n```\n'
        '```ronin-sft\n{"id": "bad_1", "family": "f", "source_volume": "T",'
        ' "license": "project-owned", "messages": [{"role": "user", "content": "x"},'
        ' {"role": "assistant", "tool_calls": [{"id": "c", "type": "function",'
        ' "function": {"name": "ghost", "arguments": {}}}]}]}\n```\n'
    )
    report = build(bad, tmp_path / "out", strict=False)
    assert report["valid"] == 1 and report["rejected"] == 1


def test_all_real_volumes_parse_clean():
    res = parse_dir(VOLUMES)
    assert res.errors == [], res.errors
    assert len(res.sft) >= 50
