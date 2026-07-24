"""D2 regression: no corpus row anywhere carries stringified tool-call args.

The valid_tool_json post-mortem's primary defect was 703/703 volume args
string-typed against the chat template's args-json-object instruction. These
tests keep the whole corpus (volumes + synthetic) object-typed forever."""
from __future__ import annotations

import json
from pathlib import Path

from ronin_training.dataset_builder import build
from ronin_training.synthetic_corpus import generate
from ronin_training.validators import validate_example

VOLUMES = Path(__file__).resolve().parents[2] / "docs" / "volumes"


def _args_of(rows):
    for r in rows:
        for m in r["messages"]:
            for c in m.get("tool_calls") or []:
                yield r, c["function"]["arguments"]


def test_volume_rows_have_no_stringified_args(tmp_path):
    build(str(VOLUMES), str(tmp_path / "out"))
    rows = [json.loads(l)
            for split in ("train", "valid", "test")
            for l in (tmp_path / "out" / f"{split}.jsonl").read_text().splitlines()]
    assert rows, "builder produced no rows"
    bad = [(r.get("id"), a) for r, a in _args_of(rows) if not isinstance(a, dict)]
    assert bad == [], f"stringified/non-object args found: {bad[:3]}"


def test_synthetic_rows_have_no_stringified_args():
    bad = [a for _, a in _args_of(generate(40, seed=7)) if not isinstance(a, dict)]
    assert bad == []


def test_schema_rejects_stringified_args():
    row = {"id": "x", "family": "f", "source_volume": "v", "license": "project-owned",
           "messages": [
               {"role": "user", "content": "q"},
               {"role": "assistant", "tool_calls": [
                   {"id": "c1", "type": "function", "function": {
                       "name": "read_file",
                       "arguments": "{\"path\": \"a.py\"}"}}]},  # the OLD defect
           ]}
    errs = validate_example(row)
    assert errs and "schema" in errs[0]
