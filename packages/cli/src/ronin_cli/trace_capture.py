"""Opt-in, LOCAL-ONLY capture of real harness runs as SFT-schema rows.

The corpus pipeline (training/src/ronin_training/synthetic_corpus.py) covers
templated tasks; this module covers the real thing — actual coding sessions,
serialized into the same ``{"messages": [...]}`` shape the dataset builder
consumes, with tool calls in the canonical dialect (OBJECT arguments).

NO-TELEMETRY IS SACRED. This module:
- is **off by default** — it does nothing unless ``RONIN_CAPTURE_TRACES`` is set
  to a directory path by the user, per shell, on purpose;
- writes **local files only** — there is no network import in this module and
  nothing here ever transmits; rows land in the directory the user named;
- captures raw session content — treat the capture dir like the session
  archive it is. The training pipeline's redaction pass
  (``ronin_training.redaction``) runs at dataset-build time, before any row can
  enter a corpus.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any

_ENV = "RONIN_CAPTURE_TRACES"


def _to_sft_message(m: Any) -> dict[str, Any]:
    """One runtime Message → the SFT-schema message shape (OpenAI-style), with
    canonical OBJECT arguments. PURE."""
    d: dict[str, Any] = {"role": getattr(m, "role", "user")}
    content = getattr(m, "content", "") or ""
    if content:
        d["content"] = content
    calls = getattr(m, "tool_calls", None) or []
    if calls:
        d["tool_calls"] = [
            {"id": c.id, "type": "function",
             "function": {"name": c.name, "arguments": dict(c.arguments or {})}}
            for c in calls
        ]
    tcid = getattr(m, "tool_call_id", None)
    if tcid:
        d["tool_call_id"] = tcid
    return d


def maybe_capture(messages: list | None, *, system: str = "") -> Path | None:
    """Append one session's structured messages as an SFT row — IF AND ONLY IF
    the user opted in via ``RONIN_CAPTURE_TRACES=<dir>``. Returns the file
    written, or None (the default). Never raises into the caller: a capture
    failure must not break a coding session."""
    target = (os.environ.get(_ENV) or "").strip()
    if not target or not messages:
        return None
    try:
        out_dir = Path(target).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        msgs = [{"role": "system", "content": system}] if system else []
        msgs += [_to_sft_message(m) for m in messages]
        if len(msgs) < 2:
            return None
        row = {
            "id": f"trace_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
            "family": "captured_trace",
            "source_volume": "trace_capture",
            "license": "project-owned",
            "messages": msgs,
        }
        path = out_dir / f"traces-{datetime.date.today().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
    except Exception:  # noqa: BLE001 — capture must never break the session
        return None
