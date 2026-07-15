"""Validate training rows + eval cases against the JSON schemas AND Ronin's real
tool registry. The registry check is what keeps the corpus honest: a training row
that calls `write_file` with the wrong arguments, or invents a tool name Ronin
doesn't have, would teach the model a protocol Ronin can't parse.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

_ROOT = Path(__file__).resolve().parents[2]   # training/
_SCHEMAS = _ROOT / "schemas"
_REGISTRY = _ROOT / "config" / "tool_registry.json"

# Assistant outputs copied from these are banned as SFT targets (spec Page 7).
_BANNED_LICENSES = {"proprietary_assistant_output", "claude_output", "gpt_output"}


@lru_cache(maxsize=None)
def _schema(name: str) -> dict:
    return json.loads((_SCHEMAS / name).read_text(encoding="utf-8"))


def _public(obj: dict) -> dict:
    """Drop internal bookkeeping keys (e.g. `_source_file` the parser stamps on)
    before schema validation — the schemas are `additionalProperties: false`, so an
    internal key would otherwise reject every row."""
    return {k: v for k, v in obj.items() if not k.startswith("_")}


@lru_cache(maxsize=None)
def load_registry() -> dict[str, dict]:
    data = json.loads(_REGISTRY.read_text(encoding="utf-8"))
    return {t["name"]: t for t in data["tools"]}


def _validate_tool_call(name: str, args_json: str, reg: dict[str, dict]) -> list[str]:
    errs: list[str] = []
    tool = reg.get(name)
    if tool is None:
        return [f"unknown tool {name!r} (not in Ronin's registry)"]
    try:
        args = json.loads(args_json)
    except json.JSONDecodeError as e:
        return [f"tool {name}: arguments are not valid JSON — {e}"]
    try:
        jsonschema.validate(args, tool["parameters"])
    except jsonschema.ValidationError as e:
        errs.append(f"tool {name}: arguments violate its schema — {e.message}")
    return errs


def validate_example(ex: dict) -> list[str]:
    """Every reason this SFT row is not usable, or [] if it is."""
    errs: list[str] = []
    try:
        jsonschema.validate(_public(ex), _schema("example.schema.json"))
    except jsonschema.ValidationError as e:
        return [f"schema: {e.message}"]
    if ex.get("license") in _BANNED_LICENSES:
        errs.append("license: proprietary-assistant output is banned as an SFT target")
    reg = load_registry()
    for msg in ex["messages"]:
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", {})
            errs += _validate_tool_call(fn.get("name", ""), fn.get("arguments", "{}"), reg)
    return errs


def validate_eval(ec: dict) -> list[str]:
    errs: list[str] = []
    try:
        jsonschema.validate(_public(ec), _schema("eval_case.schema.json"))
    except jsonschema.ValidationError as e:
        return [f"schema: {e.message}"]
    reg = load_registry()
    for t in (ec.get("must_call_tools") or []) + (ec.get("must_not_call_tools") or []):
        if t not in reg:
            errs.append(f"eval references unknown tool {t!r}")
    return errs
