from __future__ import annotations

from pathlib import Path

import pytest

from ronin_eval_suite import EvalCase, GoldenDataset


def test_roundtrip(tmp_path: Path) -> None:
    cases = [
        EvalCase(id="a", input="hello", expected="hi"),
        EvalCase(id="b", input="bye", metadata={"tag": "farewell"}),
    ]
    p = tmp_path / "ds.jsonl"
    GoldenDataset(cases).to_jsonl(p)

    loaded = GoldenDataset.from_jsonl(p)
    assert len(loaded) == 2
    assert loaded.cases[1].metadata["tag"] == "farewell"


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        GoldenDataset([EvalCase(id="x", input="1"), EvalCase(id="x", input="2")])


def test_blank_and_comment_lines_skipped(tmp_path: Path) -> None:
    p = tmp_path / "ds.jsonl"
    p.write_text(
        "# comment\n"
        '{"id": "a", "input": "hi"}\n'
        "\n"
        '{"id": "b", "input": "yo"}\n',
        encoding="utf-8",
    )
    ds = GoldenDataset.from_jsonl(p)
    assert [c.id for c in ds] == ["a", "b"]
