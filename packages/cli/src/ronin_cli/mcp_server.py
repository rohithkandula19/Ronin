"""Expose ronin itself as an MCP (Model Context Protocol) server over stdio.

This is the mirror image of ``mcp_client.py``: there, ronin is the *client* that
speaks JSON-RPC 2.0 to other servers; here, ronin *is* the server, so any MCP
client — Claude Desktop, another ronin, Cursor, an SDK — can invoke ronin's
read-only superpowers as tools.

Only READ-ONLY entry points are exposed; there is deliberately NO write/edit/
shell tool. The three tools are:

- ``ronin_ask``       — run ronin's read-only "ask" agent on a question.
- ``ronin_consensus`` — put a question to a panel of models and synthesize one
                        cross-checked answer (ronin's multi-model differentiator).
- ``ronin_research``  — search the web and answer a question with sources.

Design — pure handlers, thin shell:
    The protocol handlers (``handle_initialize``, ``handle_tools_list``,
    ``handle_tools_call``) are PURE functions: each takes a parsed JSON-RPC
    request dict and returns a response dict, with no I/O. ``handle_tools_call``
    takes the actual tool executor as an injected ``run`` callable, so tests pass
    a fake and assert the response shape without a real agent run, network, or
    stdio. ``serve()`` is the only impure part: it reads line-delimited JSON-RPC
    from stdin, routes to the handlers, and writes responses to stdout.

We never import anthropic/openai here — every tool's actual work goes through
ronin's own runner (``run_ask`` / ``run_consensus`` / ``run_research``), built
lazily inside the default executor so importing this module stays light and
side-effect free.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable

# Match the protocol version ronin's own client advertises (see mcp_client.py),
# so a ronin-talks-to-ronin handshake lines up exactly.
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ronin"

# JSON-RPC 2.0 standard error codes (the subset we emit).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# --- tool schemas ------------------------------------------------------------
# These are the public tool definitions advertised via ``tools/list``. Each is
# an MCP tool object: {name, description, inputSchema (JSON Schema)}. All are
# read-only; we mark them with annotations.readOnlyHint=True for well-behaved
# clients (advisory only — a client must not trust this for security).
TOOLS: list[dict[str, Any]] = [
    {
        "name": "ronin_ask",
        "description": (
            "Run ronin's read-only ask agent on a question. The agent uses "
            "ronin's read-only tools to investigate and returns a synthesized "
            "answer. Does NOT edit files, run shell commands, or mutate any "
            "state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask ronin's read-only agent.",
                },
            },
            "required": ["question"],
        },
        "annotations": {"readOnlyHint": True, "title": "ronin ask (read-only)"},
    },
    {
        "name": "ronin_consensus",
        "description": (
            "Put a question to a panel of several models in parallel, then "
            "synthesize one cross-checked answer. Read-only — for design, "
            "review, and decision questions. Provider-agnostic: e.g. ask "
            "Claude AND Gemini AND a local model at once and reconcile them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The question / task to put to the panel.",
                },
                "models": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Provider[:model] specs (at least 2), e.g. "
                        "['anthropic', 'gemini', 'cerebras']."
                    ),
                },
                "judge": {
                    "type": "string",
                    "description": (
                        "Optional provider[:model] to synthesize the answers "
                        "(defaults to the server's current provider)."
                    ),
                },
            },
            "required": ["task", "models"],
        },
        "annotations": {"readOnlyHint": True, "title": "ronin consensus (read-only)"},
    },
    {
        "name": "ronin_research",
        "description": (
            "Search the web and answer a question with sources. Runs ronin's "
            "read-only research agent (web search + page fetch), or degrades to "
            "a raw web search if the agent stack is unavailable. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The research question to answer from the web.",
                },
            },
            "required": ["question"],
        },
        "annotations": {"readOnlyHint": True, "title": "ronin research (read-only)"},
    },
]

# Set of exposed tool names, for quick membership checks / validation.
TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


# --- JSON-RPC envelope helpers ----------------------------------------------
def _result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    """A JSON-RPC 2.0 success envelope."""
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    """A JSON-RPC 2.0 error envelope."""
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _tool_result(req_id: Any, text: str, *, is_error: bool = False) -> dict[str, Any]:
    """A ``tools/call`` result: a single text content block.

    Mirrors the shape ronin's own client parses in ``MCPClient.call_tool`` —
    ``result.content[].text`` plus ``result.isError`` — so a ronin client can
    consume a ronin server unchanged."""
    return _result(
        req_id,
        {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        },
    )


# --- default tool executor (the impure runner, injected into handle_tools_call)
def default_run(name: str, arguments: dict[str, Any]) -> str:
    """Execute a ronin tool by name and return its answer as text.

    This is the production ``run`` injected into ``handle_tools_call``. It is the
    one place that touches ronin's heavy agent stack; everything is imported
    lazily so merely importing this module pulls in no providers and does no I/O.
    Tests inject a fake ``run`` instead and never reach here.
    """
    # Imported lazily: keep module import light and provider-free.
    from .config import load_config

    if name == "ronin_ask":
        from .runner import run_ask

        question = str(arguments.get("question", "")).strip()
        if not question:
            return "ask a question, e.g. {\"question\": \"...\"}."
        result = run_ask(load_config(), question, console=None)
        return (result.output or result.error or "(no answer)").strip()

    if name == "ronin_consensus":
        from .consensus import parse_model_spec, run_consensus

        task = str(arguments.get("task", "")).strip()
        if not task:
            return "give a task for the panel, e.g. {\"task\": \"...\"}."
        raw_models = arguments.get("models") or []
        if isinstance(raw_models, str):  # tolerate a comma-string too
            raw_models = [m for m in raw_models.split(",") if m.strip()]
        specs = [parse_model_spec(str(m)) for m in raw_models if str(m).strip()]
        if len(specs) < 2:
            return "consensus needs at least 2 models, e.g. [\"anthropic\", \"gemini\"]."
        judge = arguments.get("judge")
        judge_spec = parse_model_spec(str(judge)) if judge else None
        result = run_consensus(load_config(), task, specs, judge_spec=judge_spec)
        return (result.consensus or "(no consensus)").strip()

    if name == "ronin_research":
        from .research import run_research

        question = str(arguments.get("question", "")).strip()
        if not question:
            return "ask a research question, e.g. {\"question\": \"...\"}."
        return run_research(question, config=load_config(), root=".")

    return f"unknown tool: {name}"


# --- pure protocol handlers --------------------------------------------------
def handle_initialize(req: dict[str, Any]) -> dict[str, Any]:
    """Handle an ``initialize`` request. Pure: declares our capabilities."""
    return _result(
        req.get("id"),
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": _version()},
        },
    )


def handle_tools_list(req: dict[str, Any]) -> dict[str, Any]:
    """Handle a ``tools/list`` request. Pure: returns the static tool schemas."""
    return _result(req.get("id"), {"tools": TOOLS})


def handle_tools_call(
    req: dict[str, Any],
    *,
    run: Callable[[str, dict[str, Any]], str] = default_run,
) -> dict[str, Any]:
    """Handle a ``tools/call`` request by dispatching to ``run(name, arguments)``.

    Pure with respect to ``run``: all side effects (the actual agent invocation)
    live behind the injected callable, so tests pass a fake ``run`` returning a
    canned string and assert the envelope shape with no real agent/network/stdio.

    A protocol-level problem (unknown/missing tool name) yields a JSON-RPC
    ``error``; a tool that *runs* but fails yields a normal result with
    ``isError: true`` and the message as text — matching how MCP clients (and
    ronin's own ``MCPClient.call_tool``) distinguish the two.
    """
    req_id = req.get("id")
    params = req.get("params") or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}

    if not isinstance(name, str) or not name:
        return _error(req_id, INVALID_PARAMS, "tools/call requires a 'name'")
    if name not in TOOL_NAMES:
        return _error(req_id, METHOD_NOT_FOUND, f"unknown tool: {name}")
    if not isinstance(arguments, dict):
        return _error(req_id, INVALID_PARAMS, "'arguments' must be an object")

    try:
        text = run(name, arguments)
    except Exception as e:  # noqa: BLE001 — a tool failing is a result, not a crash
        return _tool_result(req_id, f"ERROR: {e.__class__.__name__}: {e}", is_error=True)
    return _tool_result(req_id, text if isinstance(text, str) else str(text))


def handle_request(
    req: dict[str, Any],
    *,
    run: Callable[[str, dict[str, Any]], str] = default_run,
) -> dict[str, Any] | None:
    """Route one parsed JSON-RPC request to the matching handler.

    Pure (modulo the injected ``run``). Returns the response dict, or ``None``
    for a notification (a request with no ``id``, e.g.
    ``notifications/initialized``) — notifications get no reply per JSON-RPC.
    """
    method = req.get("method")
    is_notification = "id" not in req

    if method == "initialize":
        return handle_initialize(req)
    if method == "tools/list":
        return handle_tools_list(req)
    if method == "tools/call":
        return handle_tools_call(req, run=run)
    if is_notification:
        # e.g. notifications/initialized, notifications/cancelled — no reply.
        return None
    return _error(req.get("id"), METHOD_NOT_FOUND, f"method not found: {method}")


def _version() -> str:
    """Best-effort ronin version string for ``serverInfo`` (never fatal)."""
    try:
        from ronin_cli import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return "0"


# --- the thin impure shell ---------------------------------------------------
def serve(
    *,
    stdin: Any = None,
    stdout: Any = None,
    run: Callable[[str, dict[str, Any]], str] = default_run,
) -> None:
    """Run the stdio JSON-RPC loop: read line-delimited requests from stdin,
    route them through the pure handlers, write responses to stdout.

    This is the ONLY impure part of the module. ``stdin``/``stdout`` default to
    the process streams but are injectable for completeness; ``run`` is the tool
    executor (defaults to the real ronin runner). One JSON object per line, in
    and out — the newline-delimited framing ronin's own ``MCPClient`` speaks.
    """
    inp = stdin if stdin is not None else sys.stdin
    out = stdout if stdout is not None else sys.stdout

    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            _write(out, _error(None, PARSE_ERROR, "parse error: invalid JSON"))
            continue
        if not isinstance(req, dict):
            _write(out, _error(None, INVALID_REQUEST, "invalid request: not an object"))
            continue
        try:
            resp = handle_request(req, run=run)
        except Exception as e:  # noqa: BLE001 — never let the loop die on one bad request
            _write(out, _error(req.get("id"), INTERNAL_ERROR, f"{e.__class__.__name__}: {e}"))
            continue
        if resp is not None:
            _write(out, resp)


def _write(out: Any, obj: dict[str, Any]) -> None:
    """Write one JSON-RPC message as a single line and flush."""
    out.write(json.dumps(obj) + "\n")
    try:
        out.flush()
    except Exception:  # noqa: BLE001 — some streams don't flush
        pass
