"""Tests for style — sessions → fine-tuning dataset."""
from __future__ import annotations

import json

from ronin_cli.style import Example, build_dataset, to_jsonl, transcript_to_examples


def test_pairs_user_assistant() -> None:
    t = ["USER: how do I add a flag to the CLI here?",
         "ASSISTANT: add a typer.Option in main.py and read it in the body"]
    ex = transcript_to_examples(t)
    assert len(ex) == 1
    assert ex[0].instruction.startswith("how do I add")
    assert "typer.Option" in ex[0].response


def test_drops_trivial_turns() -> None:
    t = ["USER: hi", "ASSISTANT: yo"]            # both too short
    assert transcript_to_examples(t) == []


def test_handles_unpaired() -> None:
    t = ["USER: a real question about the codebase here",
         "USER: another question with no answer in between either"]
    assert transcript_to_examples(t) == []        # no assistant responses


def test_build_dataset_shape() -> None:
    t = [["USER: implement a retry helper please",
          "ASSISTANT: here is a retry helper with exponential backoff applied"]]
    rows = build_dataset(t)
    assert len(rows) == 1
    msgs = rows[0]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_build_dataset_with_system() -> None:
    t = [["USER: add a test for the parser module here",
          "ASSISTANT: added a pytest covering the empty-input edge case"]]
    rows = build_dataset(t, system="Always use pytest.")
    msgs = rows[0]["messages"]
    assert msgs[0] == {"role": "system", "content": "Always use pytest."}
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]


def test_to_jsonl_is_valid() -> None:
    rows = build_dataset([["USER: a sufficiently long instruction here",
                           "ASSISTANT: a sufficiently long and useful response here"]])
    text = to_jsonl(rows)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert "messages" in parsed
