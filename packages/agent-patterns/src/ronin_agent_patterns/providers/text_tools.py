"""Recover tool calls that weaker/open models emit as TEXT, not structured calls.

Frontier models (Claude, GPT-4o) return tool calls in the structured
``tool_calls`` field. Many open models (gpt-oss, Llama, Qwen, DeepSeek on some
endpoints) instead *write* the call into the message content — as a
``<tool_call>{…}</tool_call>`` block, a fenced ```json block, or a bare JSON
object. When that happens the structured field is empty and the agent treats the
prose as a final answer — so it "just chats" and never acts.

This module extracts those text-embedded calls and converts them to real
``ToolCall``s. It is deliberately conservative: a candidate is only accepted if
its name matches a tool that's actually registered, so ordinary JSON in an
answer isn't mistaken for a call. Fully pure + unit-tested.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Iterable

from .base import ToolCall

# <tool_call>{...}</tool_call>, <function_call>{...}</function_call>, [TOOL_CALL]{...}
_TAG_RE = re.compile(
    r"<(?:tool_call|function_call|function)>\s*(\{.*?\})\s*</(?:tool_call|function_call|function)>",
    re.DOTALL | re.IGNORECASE)
# fenced ```json { ... } ``` (or ```tool_call / bare ```)
_FENCE_RE = re.compile(r"```(?:json|tool_call|tool|python)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def _find_brace_objects(text: str) -> list[str]:
    """Top-level {...} substrings via brace matching (catches JSON in prose)."""
    out: list[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append(text[start:i + 1])
                    start = -1
    return out


def _coerce(obj: Any, valid: set[str]) -> tuple[str, dict] | None:
    """Pull (name, args) from a dict if it names a registered tool."""
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function") if isinstance(obj.get("function"), dict) else {}
    name = (obj.get("name") or obj.get("tool") or obj.get("tool_name")
            or obj.get("action") or fn.get("name"))
    if name not in valid:
        return None
    args = (obj.get("arguments") or obj.get("parameters") or obj.get("args")
            or obj.get("action_input") or fn.get("arguments") or {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, TypeError):
            args = {}
    return name, (args if isinstance(args, dict) else {})


def extract_text_tool_calls(text: str, valid_names: Iterable[str]) -> list[ToolCall]:
    """Find tool calls written into ``text`` whose name is in ``valid_names``.

    Scans, in order: <tool_call> tags, fenced code blocks, and any balanced
    top-level JSON object. De-duplicates identical (name, args) pairs. Returns
    real ToolCalls with synthetic ids (text-0, text-1, …)."""
    if not text or not text.strip():
        return []
    valid = set(valid_names)
    if not valid:
        return []

    candidates: list[str] = []
    candidates += _TAG_RE.findall(text)
    candidates += _FENCE_RE.findall(text)
    candidates += _find_brace_objects(text)

    found: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for item in (obj if isinstance(obj, list) else [obj]):
            coerced = _coerce(item, valid)
            if coerced is None:
                continue
            key = coerced[0] + "|" + json.dumps(coerced[1], sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                found.append(coerced)

    # IDs must be GLOBALLY unique, not per-response. Open models emit tool calls
    # as text every response; with persistent cross-turn history these ids pile up
    # in one conversation, and duplicates (`text-0` reused each turn) make
    # tool_use↔tool_result mapping ambiguous — Anthropic rejects non-unique
    # tool_use ids with a 400 when an Ollama-built history is later routed to it.
    # A short uuid per recovered call keeps every id distinct across turns/providers.
    return [ToolCall(id=f"text-{uuid.uuid4().hex[:8]}", name=n, arguments=a) for (n, a) in found]
