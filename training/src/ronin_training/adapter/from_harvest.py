"""Bridge ``ronin harvest``'s sharegpt output into the trainer's ``messages`` rows.

The flywheel has two halves that were built to different contracts. ``ronin harvest``
(``src/ronin/evals/harvest.py`` in the main tree) records real eval trajectories and
writes **sharegpt** SFT rows — ``{"task_id", "conversations": [{"from", "value"}],
"assistant_spans": [...]}`` — where every turn is one rendered string and tool calls are
inlined as ``<tool_call>{json}</tool_call>`` text. This project's SFT pipeline
(:mod:`ronin_training.sft.pipeline`) consumes the mlx/chat shape instead — ``{"task_id",
"messages": [{"role", "content", "tool_calls"}]}`` — and derives its assistant-only loss
mask from ``role == "assistant"``. The two never met: feeding a sharegpt row straight to
:func:`ronin_training.adapter.dataset.examples_from_rows` raises ``DatasetError`` for a
missing ``messages`` key, so the recorded trajectories yielded **zero** training rows.

This module is that missing converter, and it is deliberately faithful about one thing:
**tool calls are reconstructed as structure, not left as text.** ``render_tool_call`` in
the producer emits ``<tool_call>{"arguments": {...}, "name": "..."}</tool_call>``; this
parses those blocks back into ``{"function": {"name", "arguments"}}`` so the assistant
turn a row teaches is a *structured* call — which is what the offline tool-validity hook
(:func:`ronin_training.sft.eval_hook.calls_from_rows`) reads, and getting tool syntax
right is the entire reason this small model is being trained (see
``training/reports/valid_tool_json_root_cause.md``). A block whose body is not valid JSON
is left inline in ``content`` rather than dropped — a malformed call is still assistant
text the model produced, and hiding it would flatter the validity score.

**Scope: SFT only.** DPO and RLVR are not bridged here, on purpose:

* The DPO trainer (:mod:`ronin_training.dpo.pairs`) is a *behavioural-taxonomy* generator
  — each pair carries a ``pref_class`` (malformed JSON, blind edit, ignored denial, …)
  and often a *synthesized* corrected turn. ``harvest``'s generic ``{prompt, chosen,
  rejected}`` strings carry no such class, and inventing one to force them through would
  fabricate the very label the trainer trains on.
* The RL environment (:mod:`ronin_training.rl.environment`) is *online*: it needs a live
  task directory, a policy, and a hidden test, and scores a reward it computes itself.
  ``harvest``'s offline ``rlvr.jsonl`` reward record is a different paradigm, not a row it
  can load.

So this bridge closes the one seam that *is* a data conversion, and the two that are not
stay explicitly unbridged rather than faked.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ronin_training.adapter.dataset import read_jsonl

#: sharegpt ``from`` tag -> chat ``role``. The inverse of the producer's ``FROM_TAG``
#: (``ronin.evals.harvest.FROM_TAG``); kept here as the one place the mapping is asserted
#: on this side, so a tag rename over there fails a test rather than silently mislabels a
#: turn (which would poison the assistant-only mask).
ROLE_OF_TAG: dict[str, str] = {
    "system": "system",
    "human": "user",
    "gpt": "assistant",
    "tool": "tool",
}

#: The producer's tool-call wire format, ``<tool_call>{json}</tool_call>`` — see
#: ``ronin.evals.harvest.render_tool_call``. Non-greedy and DOTALL so a call whose
#: arguments contain newlines is still captured as one block.
_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


class HarvestBridgeError(ValueError):
    """A harvested sharegpt row could not be converted to a trainer row."""


def _structured_call(body: str) -> dict[str, Any] | None:
    """One ``<tool_call>`` body as ``{"function": {"name", "arguments"}}``, or ``None``.

    ``None`` when the body is not the ``{"name", "arguments"}`` JSON the producer writes —
    the caller then leaves the raw block in ``content`` rather than emit a call with an
    empty name, which would score as invalid and misreport the dataset's real syntax rate.
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, Mapping) or "name" not in payload:
        return None
    return {"function": {"name": payload["name"], "arguments": payload.get("arguments", {})}}


def _split_tool_calls(value: str) -> tuple[str, list[dict[str, Any]]]:
    """Separate an assistant ``value`` into ``(prose, structured tool_calls)``.

    Every ``<tool_call>`` block whose body parses is lifted out into a structured call;
    the remaining text is returned as the message content with the parsed blocks removed
    and blank lines dropped. A block that does not parse is left in the text.
    """
    calls: list[dict[str, Any]] = []

    def _take(match: re.Match[str]) -> str:
        call = _structured_call(match.group(1))
        if call is None:
            return match.group(0)  # not JSON we understand — keep the raw block as text
        calls.append(call)
        return ""

    prose = _TOOL_CALL_RE.sub(_take, value)
    prose = "\n".join(line for line in prose.splitlines() if line.strip())
    return prose.strip(), calls


def message_from_turn(turn: Mapping[str, Any]) -> dict[str, Any]:
    """One sharegpt ``{"from", "value"}`` turn as a chat ``{"role", "content"[, ...]}``.

    Assistant turns get their inlined ``<tool_call>`` blocks reconstructed into a
    structured ``tool_calls`` list; every other role is a plain ``role``/``content`` pair.
    """
    tag = str(turn.get("from", ""))
    role = ROLE_OF_TAG.get(tag)
    if role is None:
        raise HarvestBridgeError(
            f"unknown sharegpt 'from' tag {tag!r} — expected one of {sorted(ROLE_OF_TAG)}"
        )
    value = str(turn.get("value", ""))
    if role != "assistant":
        return {"role": role, "content": value}
    content, tool_calls = _split_tool_calls(value)
    message: dict[str, Any] = {"role": role, "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def sft_row_from_sharegpt(record: Mapping[str, Any]) -> dict[str, Any]:
    """One harvested sharegpt SFT record as a trainer ``{"task_id", "messages"}`` row.

    Raises when the record is not a sharegpt SFT record (no ``conversations`` list), so a
    file of the wrong shape fails loudly here rather than as an empty mask downstream.
    """
    conversations = record.get("conversations")
    if not isinstance(conversations, Sequence) or isinstance(conversations, (str, bytes)):
        raise HarvestBridgeError(
            "record has no 'conversations' list — is this a harvest sft.jsonl row?"
        )
    messages = [message_from_turn(turn) for turn in conversations]
    row: dict[str, Any] = {"messages": messages}
    task_id = record.get("task_id")
    if task_id:
        row["task_id"] = task_id
    return row


def sft_rows_from_sharegpt(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert a stream of harvested sharegpt records to trainer SFT rows."""
    return [sft_row_from_sharegpt(record) for record in records]


def load_sft_rows(path: str | Path) -> list[dict[str, Any]]:
    """Read a harvest ``sft.jsonl`` and return trainer-ready ``{"task_id", "messages"}`` rows.

    The one call a training run makes to consume ``ronin harvest`` output:
    ``plan_sft_run(load_sft_rows("out/sft.jsonl"), profile, tokenize=...)``.
    """
    return sft_rows_from_sharegpt(read_jsonl(path))


__all__ = [
    "ROLE_OF_TAG",
    "HarvestBridgeError",
    "load_sft_rows",
    "message_from_turn",
    "sft_row_from_sharegpt",
    "sft_rows_from_sharegpt",
]
