"""Extract machine-readable training rows and eval cases from Ronin volumes.

A volume is a Markdown file (`docs/volumes/volume_*.md`) whose human-readable law
is interleaved with fenced blocks the builder consumes:

    ```ronin-sft
    {"id": "...", "family": "...", "source_volume": "I", "license": "project-owned",
     "messages": [...], "tools": [...]}
    ```

    ```ronin-eval
    {"eval_id": "...", "category": "gate_respect", "prompt": "...",
     "must_not_call_tools": ["run_command"], "must_include": ["confirm"]}
    ```

This module is PURE (string in, dicts out) and deterministic. A malformed block is
reported with its file + block index, never silently dropped.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_FENCE = re.compile(r"```ronin-(sft|eval)\s*\n(.*?)\n```", re.DOTALL)


@dataclass
class ParseResult:
    sft: list[dict] = field(default_factory=list)
    evals: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_text(text: str, *, source: str = "<text>") -> ParseResult:
    res = ParseResult()
    for i, m in enumerate(_FENCE.finditer(text)):
        kind, body = m.group(1), m.group(2)
        try:
            obj = json.loads(body)
        except json.JSONDecodeError as e:
            res.errors.append(f"{source} block #{i} ({kind}): invalid JSON — {e}")
            continue
        if not isinstance(obj, dict):
            res.errors.append(f"{source} block #{i} ({kind}): not a JSON object")
            continue
        obj.setdefault("_source_file", source)
        (res.sft if kind == "sft" else res.evals).append(obj)
    return res


def parse_volume(path: Path) -> ParseResult:
    return parse_text(path.read_text(encoding="utf-8"), source=str(path.name))


def parse_dir(volumes_dir: Path | str) -> ParseResult:
    """Parse every `volume_*.md` under a directory, in sorted (deterministic) order."""
    d = Path(volumes_dir)
    out = ParseResult()
    for p in sorted(d.glob("volume_*.md")):
        r = parse_volume(p)
        out.sft.extend(r.sft)
        out.evals.extend(r.evals)
        out.errors.extend(r.errors)
    return out
