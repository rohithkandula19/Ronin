from __future__ import annotations

import inspect
import json
from typing import Any

import anthropic

from .types import MALFORMED_ARGS_KEY, Tool

DEFAULT_MODEL = "claude-sonnet-4-6"

# Families of interchangeable argument names. Weak models routinely pick a
# plausible-but-wrong name (``path`` for ``directory``, ``cmd`` for ``command``);
# if the handler has a parameter in the same family, we remap rather than fail.
_ALIAS_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"path", "file", "file_path", "filepath", "filename", "fname"}),
    frozenset({"directory", "dir", "folder", "path", "root", "cwd"}),
    frozenset({"content", "text", "body", "data", "new_content", "contents"}),
    frozenset({"command", "cmd", "shell", "script"}),
    frozenset({"query", "q", "search", "term", "pattern", "keyword"}),
    frozenset({"url", "uri", "link", "address"}),
    frozenset({"id", "pid", "process_id", "identifier"}),
)


def make_client(api_key: str | None = None) -> anthropic.Anthropic:
    """Build an Anthropic client. ``api_key=None`` falls back to ``ANTHROPIC_API_KEY``."""
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _adapt_args(handler: Any, args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Reconcile model-supplied ``args`` with the handler's real signature.

    Returns (adapted_args, dropped_keys). A handler that accepts ``**kwargs`` gets
    everything verbatim. Otherwise unknown keys are remapped to an unfilled
    parameter in the same alias family when possible, and dropped otherwise — so
    a model's near-miss argument name no longer crashes the call."""
    try:
        params = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return args, []  # can't introspect (builtin/C) → pass through
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return args, []
    known = {name for name, p in params.items()
             if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    adapted: dict[str, Any] = {}
    leftovers: dict[str, Any] = {}
    for key, val in args.items():
        (adapted if key in known else leftovers)[key] = val
    dropped: list[str] = []
    for key, val in leftovers.items():
        target = next(
            (kp for fam in _ALIAS_FAMILIES if key in fam
             for kp in known if kp in fam and kp not in adapted),
            None,
        )
        if target is not None:
            adapted[target] = val
        else:
            dropped.append(key)
    return adapted, dropped


def _signature_hint(tool: Tool) -> str:
    """A compact 'expected arguments' hint from the tool's input schema."""
    props = (tool.input_schema or {}).get("properties", {})
    required = set((tool.input_schema or {}).get("required", []))
    if not props:
        return "(no arguments)"
    parts = [f"{n}{'' if n in required else '?'}" for n in props]
    return ", ".join(parts)


def execute_tool_call(tool: Tool, args: dict[str, Any]) -> tuple[str, bool]:
    """Run a tool. Returns ``(result_str, is_error)``.

    Robust to the argument mistakes weak models make: near-miss arg names are
    remapped to the handler's real parameters, and an argument mismatch yields a
    *coaching* error that states the expected arguments (so the model retries
    correctly) instead of a raw ``TypeError``. Other exceptions are still caught
    and surfaced so the agent can recover.
    """
    if isinstance(args, dict) and MALFORMED_ARGS_KEY in args:
        # The provider could not parse this call's JSON arguments. Tell the model
        # its JSON was malformed (quoting the raw text) — a distinct failure from
        # a wrong argument name, so it fixes the JSON instead of renaming args.
        raw = args[MALFORMED_ARGS_KEY]
        return (f"ERROR calling {tool.name}: your tool-call arguments were not "
                f"valid JSON, so the call could not run. Raw arguments received: "
                f"{raw!r}. Re-emit the call with valid JSON matching: "
                f"{_signature_hint(tool)} ('?' marks optional).", True)

    if not isinstance(args, dict):
        try:
            out = tool.handler(args)
        except Exception as exc:  # noqa: BLE001
            return f"{type(exc).__name__}: {exc}", True
        return (out, False) if isinstance(out, str) else (json.dumps(out, default=str), False)

    adapted, _dropped = _adapt_args(tool.handler, args)
    try:
        out = tool.handler(**adapted)
    except TypeError as exc:
        # Almost always a wrong/missing argument name → coach the model.
        return (f"ERROR calling {tool.name}: {exc}. "
                f"Expected arguments: {_signature_hint(tool)} "
                f"('?' marks optional). Retry with exactly those names.", True)
    except Exception as exc:  # noqa: BLE001 — agent must see the error to recover
        return f"{type(exc).__name__}: {exc}", True
    if isinstance(out, str):
        return out, False
    return json.dumps(out, default=str), False


def text_from_response(response: Any) -> str:
    """Extract assistant text from an Anthropic response, ignoring tool_use blocks."""
    parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def has_tool_use(response: Any) -> bool:
    return any(getattr(b, "type", None) == "tool_use" for b in response.content)
