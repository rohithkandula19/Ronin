"""ronin-dialect — THE canonical tool-call wire format, in one place.

The `valid_tool_json` post-mortem (training/reports/valid_tool_json_root_cause.md)
found three independent implementations of the ``<tool_call>`` dialect — the
corpus's template rendering, the embedded runtime parser, and the eval extractor —
that merely *happened* to agree. This module is the single source of truth all
three import, so they can never drift again:

- ``render_tool_call`` / ``render_tool_call_message`` — corpus generation renders
  calls through this, never by hand.
- ``parse_tool_calls`` — the strict runtime parser (moved verbatim from
  ``ronin_cli.embedded_provider``; that module now delegates here).
- ``extract_call_names`` — the eval's runtime-parity extractor
  (``ronin_training.eval_runner`` delegates here).

Canonical form (matches Qwen2.5-Coder's own chat-template instruction — arguments
as a JSON *object*, not a stringified object):

    <tool_call>
    {"name": "read_file", "arguments": {"path": "src/config.py"}}
    </tool_call>

Legacy corpus rows carry string-typed arguments; the parser keeps accepting them
(double-decoded) so nothing already shipped breaks. Stdlib only — this package
must stay importable by both ``ronin-cli`` and ``ronin-training`` with zero
dependency weight.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

TOOL_CALL_OPEN = "<tool_call>"
TOOL_CALL_CLOSE = "</tool_call>"

# One JSON object per block. Non-greedy body, whitespace-tolerant, spans newlines.
BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)

# Runtime-parity call shape: an object opening with a "name" key (registry-style
# [a-z_]+ value) immediately followed by an "arguments" key. Mirrors what the
# eval has always required — a call the runtime would actually execute.
CALL_SHAPE_RE = re.compile(r'\{\s*"name"\s*:\s*"([a-z_]+)"\s*,\s*"arguments"')


@dataclass
class ParsedCall:
    """One well-formed tool call recovered from generated text. PURE data."""
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


def render_tool_call(name: str, arguments: dict[str, Any]) -> str:
    """The canonical ``<tool_call>`` block for one call. PURE.

    ``arguments`` is rendered as a JSON *object* — the format the model's own
    chat template instructs at inference — with deterministic key order.
    """
    if not name or not isinstance(name, str):
        raise ValueError("tool call needs a non-empty string name")
    if not isinstance(arguments, dict):
        raise ValueError("tool call arguments must be a dict")
    # Top-level key order is LOAD-BEARING: the runtime-parity shape is
    # {"name": ..., "arguments": ...} — name first. Only the arguments object is
    # key-sorted, for deterministic bytes regardless of caller dict order.
    args_json = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    body = f'{{"name": {json.dumps(name, ensure_ascii=False)}, "arguments": {args_json}}}'
    return f"{TOOL_CALL_OPEN}\n{body}\n{TOOL_CALL_CLOSE}"


def render_tool_call_message(calls: list[dict[str, Any]]) -> str:
    """Render a whole assistant turn's calls (``[{"name":…, "arguments":…}]``)
    the way the chat template lays them out: one block per call, newline-joined."""
    return "\n".join(render_tool_call(c["name"], c.get("arguments") or {}) for c in calls)


def parse_tool_calls(text: str) -> tuple[str, list[ParsedCall]]:
    """Extract ``<tool_call>`` blocks from generated text. PURE — no I/O.

    Returns ``(clean_text, calls)`` where ``clean_text`` has parsed blocks removed.
    A block whose body isn't a JSON object with a string ``name`` stays in the
    text verbatim — a malformed call is surfaced, never silently dropped or
    "repaired" into a call the model didn't make. ``arguments`` may be a JSON
    object or (legacy corpus rows / some templates) a JSON-encoded string of one.
    """
    calls: list[ParsedCall] = []
    kept: list[str] = []
    cursor = 0
    for m in BLOCK_RE.finditer(text):
        kept.append(text[cursor:m.start()])
        cursor = m.end()
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            kept.append(m.group(0))  # malformed: keep visible in the text
            continue
        name = obj.get("name") if isinstance(obj, dict) else None
        if not isinstance(name, str) or not name:
            kept.append(m.group(0))
            continue
        args = obj.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                kept.append(m.group(0))
                continue
        if not isinstance(args, dict):
            kept.append(m.group(0))
            continue
        calls.append(ParsedCall(name=name, arguments=args))
    kept.append(text[cursor:])
    return "".join(kept).strip(), calls


def extract_call_names(raw: str) -> list[str]:
    """Every wrapper-enclosed, call-shaped tool name in ``raw`` — the eval's
    runtime-parity extraction, INCLUDING names not in any registry (hallucination
    detection is the caller's job)."""
    names: list[str] = []
    for chunk in re.findall(r"<tool_call>(.*?)</tool_call>", raw, re.DOTALL):
        names.extend(CALL_SHAPE_RE.findall(chunk))
    return names


__all__ = [
    "TOOL_CALL_OPEN", "TOOL_CALL_CLOSE", "BLOCK_RE", "CALL_SHAPE_RE",
    "ParsedCall", "render_tool_call", "render_tool_call_message",
    "parse_tool_calls", "extract_call_names",
]
