"""The harvest→trainer bridge: sharegpt rows become SFT ``messages`` rows that train.

The end-to-end test is the one that matters: it takes records in exactly the shape
``ronin harvest`` writes to ``sft.jsonl`` (sharegpt, tool calls inlined as
``<tool_call>{json}</tool_call>``), runs them through the bridge, and feeds the result to
the real offline SFT dry-run. Before this bridge existed that path yielded zero rows; the
test asserts the loop is closed — train rows exist, every one is learnable, and the
reconstructed tool calls score as valid.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from ronin_training.adapter.from_harvest import (
    HarvestBridgeError,
    load_sft_rows,
    message_from_turn,
    sft_row_from_sharegpt,
    sft_rows_from_sharegpt,
)
from ronin_training.sft.pipeline import plan_sft_run
from ronin_training.sft.profiles import QWEN7B_QLORA

_KNOWN = frozenset({"read", "glob"})


def _tok(text: str) -> Sequence[int]:
    return [len(w) for w in text.split()]


def _tool_call(name: str, arguments: dict[str, Any]) -> str:
    """The producer's wire format, mirrored — ``ronin.evals.harvest.render_tool_call``."""
    body = json.dumps({"name": name, "arguments": arguments}, sort_keys=True, ensure_ascii=False)
    return f"<tool_call>{body}</tool_call>"


def _sharegpt_row(task: str, tool: str) -> dict[str, Any]:
    """One harvested sharegpt SFT record, in exactly ``to_sharegpt``'s shape."""
    return {
        "task_id": task,
        "conversations": [
            {"from": "system", "value": "you are ronin"},
            {"from": "human", "value": f"work on {task}"},
            {"from": "gpt", "value": f"on it\n{_tool_call(tool, {'path': 'x'})}"},
            {"from": "tool", "value": "ok result"},
            {"from": "gpt", "value": "done"},
        ],
        "assistant_spans": [2, 4],
    }


def _corpus(n_tasks: int = 12) -> list[dict[str, Any]]:
    return [_sharegpt_row(f"task-{i:02d}", "read" if i % 2 else "glob") for i in range(n_tasks)]


# --------------------------------------------------------------------------- #
# Per-turn conversion
# --------------------------------------------------------------------------- #


def test_tags_map_to_chat_roles() -> None:
    assert message_from_turn({"from": "system", "value": "s"}) == {"role": "system", "content": "s"}
    assert message_from_turn({"from": "human", "value": "u"}) == {"role": "user", "content": "u"}
    assert message_from_turn({"from": "tool", "value": "r"}) == {"role": "tool", "content": "r"}


def test_an_unknown_tag_is_a_named_error() -> None:
    with pytest.raises(HarvestBridgeError, match="unknown sharegpt 'from' tag"):
        message_from_turn({"from": "assistant", "value": "x"})  # sharegpt uses 'gpt', not this


def test_assistant_tool_calls_are_reconstructed_as_structure() -> None:
    turn = {"from": "gpt", "value": f"looking\n{_tool_call('read', {'path': 'app.py'})}"}
    message = message_from_turn(turn)
    assert message["role"] == "assistant"
    assert message["content"] == "looking"  # the <tool_call> block is lifted out of the prose
    assert message["tool_calls"] == [
        {"function": {"name": "read", "arguments": {"path": "app.py"}}}
    ]


def test_a_malformed_tool_call_stays_inline_and_adds_no_structured_call() -> None:
    turn = {"from": "gpt", "value": "trying <tool_call>not json</tool_call>"}
    message = message_from_turn(turn)
    assert "tool_calls" not in message
    assert "<tool_call>not json</tool_call>" in message["content"]


def test_an_assistant_turn_with_no_tool_call_has_no_tool_calls_key() -> None:
    assert message_from_turn({"from": "gpt", "value": "done"}) == {
        "role": "assistant",
        "content": "done",
    }


# --------------------------------------------------------------------------- #
# Row conversion
# --------------------------------------------------------------------------- #


def test_a_record_without_conversations_is_a_named_error() -> None:
    with pytest.raises(HarvestBridgeError, match="no 'conversations' list"):
        sft_row_from_sharegpt({"task_id": "t", "messages": []})


def test_row_carries_task_id_and_messages() -> None:
    row = sft_row_from_sharegpt(_sharegpt_row("task-00", "read"))
    assert row["task_id"] == "task-00"
    assert [m["role"] for m in row["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


def test_load_sft_rows_reads_a_harvest_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "sft.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in _corpus(3):
            handle.write(json.dumps(record) + "\n")
    rows = load_sft_rows(path)
    assert len(rows) == 3
    assert all("messages" in row for row in rows)


# --------------------------------------------------------------------------- #
# The loop closed: harvest output → offline SFT dry-run
# --------------------------------------------------------------------------- #


def test_harvest_output_flows_through_the_sft_dry_run() -> None:
    rows = sft_rows_from_sharegpt(_corpus(12))
    plan = plan_sft_run(rows, QWEN7B_QLORA, tokenize=_tok, seed=0, known_tools=_KNOWN)
    assert plan.train_examples > 0 and plan.holdout_examples > 0
    assert plan.all_learnable, "every converted train row has a learnable assistant token"
    # The reconstructed tool calls are known-name + dict-args, so validity is a real 1.0 —
    # not the vacuous None that inlined-text calls would have produced.
    assert plan.holdout_validity.rate == 1.0
