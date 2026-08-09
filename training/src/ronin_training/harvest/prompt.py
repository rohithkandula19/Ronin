"""Render a trajectory into the harness's exact prompt format. One renderer, no second.

This module is where the previous cycle's most expensive bug lives or dies. The
`valid_tool_json` 0/4 post-mortem (``training/reports/valid_tool_json_root_cause.md``)
found three independent implementations of the ``<tool_call>`` dialect that merely
happened to agree, and two prompt-distribution divergences (D1: rows carried only
the tools they used while inference presents the whole registry; D2: string-typed
arguments contradicting the template's own instruction). A training example whose
prompt differs from what inference sends produces a model *worse* than the base.

So nothing here invents a format:

* the **tool-call wire text** comes from ``ronin_dialect.render_tool_call_message``,
  the canonical module the runtime parser and the eval extractor already share;
* the **tools block** is the ``{"type": "function", "function": {...}}`` shape every
  existing builder emits, built from the toolset *the run actually had* (D1);
* **arguments are JSON objects**, never JSON-encoded strings (D2);
* the **row shape** is ``{"messages": [...], "tools": [...]}`` — the pair the
  trainer hands to ``tokenizer.apply_chat_template(messages, tools=tools)``
  (``training/cuda/train_cuda.py``) and the shape ``example.schema.json`` describes.

The prompt *string* is deliberately not produced here. The template that turns
``(messages, tools)`` into text belongs to the model's own tokenizer, and the one
lesson of the post-mortem is that a second implementation of it is a liability
even when it agrees. Callers that need text inject the tokenizer's renderer; see
:func:`~ronin_training.harvest.dpo.to_text_row`.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .trajectory import RecordedCall, Step, ToolSchema, Trajectory

# ronin_dialect is stdlib-only and ships no py.typed marker, so strict mypy cannot
# see through it. Narrowly ignored and re-typed by the thin wrappers below rather
# than reimplemented — reimplementing it is precisely divergence D3.
from ronin_dialect import (  # type: ignore[import-untyped]
    parse_tool_calls as _dialect_parse,
)
from ronin_dialect import (  # type: ignore[import-untyped]
    render_tool_call_message as _dialect_render,
)

SYSTEM_ROLE = "system"
USER_ROLE = "user"
ASSISTANT_ROLE = "assistant"
TOOL_ROLE = "tool"


def tools_block(tools: Sequence[ToolSchema]) -> tuple[dict[str, Any], ...]:
    """The toolset in the OpenAI function shape a chat template's ``tools=`` wants.

    Byte-identical to what ``ronin_training.eval_runner.ronin_tools_for_template``
    produces for the same toolset — pinned by a parity test, because the *whole*
    point of presenting the full toolset (divergence D1) is defeated if training
    presents it in a different shape than the eval does.
    """
    return tuple(
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": dict(t.parameters),
            },
        }
        for t in tools
    )


def call_object(call: RecordedCall) -> dict[str, Any]:
    """One tool call in the corpus's OpenAI shape, with **object** arguments (D2)."""
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": dict(call.arguments)},
    }


def assistant_message(step: Step) -> dict[str, Any]:
    """The assistant turn as a chat message. ``tool_calls`` omitted when there are none."""
    message: dict[str, Any] = {"role": ASSISTANT_ROLE, "content": step.text}
    if step.calls:
        message["tool_calls"] = [call_object(c) for c in step.calls]
    return message


def assistant_target_text(step: Step) -> str:
    """The same turn as the **wire text** the runtime parses, via ``ronin_dialect``.

    Used where a target has to be a string (a preference pair's rejected/chosen
    side rendered for a text-format trainer) and by the round-trip test that proves
    a rendered target parses back to exactly the calls the trajectory recorded.
    """
    if not step.calls:
        return step.text
    blocks: str = _dialect_render([{"name": c.name, "arguments": dict(c.arguments)} for c in step.calls])
    return f"{step.text}\n{blocks}" if step.text else blocks


def parse_target_text(text: str) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """``(name, arguments)`` for every call the **runtime** would execute in ``text``.

    Delegates to the canonical parser, so a test can assert that what we train as
    the target is what the runtime would actually run — the exact link that was
    never checked when tool syntax scored 0/4.
    """
    _clean, calls = _dialect_parse(text)
    return tuple((c.name, dict(c.arguments)) for c in calls)


def tool_messages(step: Step) -> tuple[dict[str, Any], ...]:
    """The observations for one step, in the order the calls were made.

    A call with no recorded result is skipped rather than given an invented one: a
    fabricated observation would teach the model that an unanswered call is normal.
    """
    messages: list[dict[str, Any]] = []
    for call in step.calls:
        result = step.result_for(call.id)
        if result is None:
            continue
        messages.append(
            {"role": TOOL_ROLE, "tool_call_id": call.id, "content": result.observation()}
        )
    return tuple(messages)


@dataclass(frozen=True, slots=True)
class PromptPayload:
    """``(messages, tools)`` — the whole prompt, in the form the trainer consumes.

    Frozen and hashable-by-digest so two renderings can be compared for byte
    equality, which is the only way "the training prompt equals the inference
    prompt" is a fact rather than a hope.
    """

    messages: tuple[Mapping[str, Any], ...]
    tools: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("PromptPayload.messages cannot be empty")

    def as_row(self) -> dict[str, Any]:
        """The trainer-facing row: only ``messages`` and ``tools``, exactly as
        ``dataset_builder`` exports and ``train_cuda.py`` renders."""
        row: dict[str, Any] = {"messages": [dict(m) for m in self.messages]}
        if self.tools:
            row["tools"] = [dict(t) for t in self.tools]
        return row

    def with_completion(self, completion: Mapping[str, Any]) -> PromptPayload:
        return PromptPayload(messages=(*self.messages, completion), tools=self.tools)

    def digest(self) -> str:
        """A stable content hash. Deduplication and byte-equality assertions."""
        blob = json.dumps(self.as_row(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def render_prefix(trajectory: Trajectory, upto_step: int) -> PromptPayload:
    """System + tools + history up to (excluding) ``upto_step``.

    The history is the *recorded* one: earlier assistant turns with their object
    arguments and their real observations. Presenting the full toolset on every
    row is deliberate (divergence D1) — the model is evaluated choosing one tool
    out of the whole registry, so that is the prompt it must be trained under.
    """
    if not 0 <= upto_step <= len(trajectory.steps):
        raise IndexError(
            f"upto_step {upto_step} is outside trajectory {trajectory.run_id!r} "
            f"with {len(trajectory.steps)} step(s)"
        )
    messages: list[Mapping[str, Any]] = []
    if trajectory.system:
        messages.append({"role": SYSTEM_ROLE, "content": trajectory.system})
    messages.append({"role": USER_ROLE, "content": trajectory.prompt})
    for step in trajectory.steps[:upto_step]:
        messages.append(assistant_message(step))
        messages.extend(tool_messages(step))
    return PromptPayload(messages=tuple(messages), tools=tools_block(trajectory.tools))


__all__ = [
    "ASSISTANT_ROLE",
    "SYSTEM_ROLE",
    "TOOL_ROLE",
    "USER_ROLE",
    "PromptPayload",
    "assistant_message",
    "assistant_target_text",
    "call_object",
    "parse_target_text",
    "render_prefix",
    "tool_messages",
    "tools_block",
]
