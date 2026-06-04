"""Remote MCP transport — connect to hosted MCP servers over HTTP.

ronin's stdio client (mcp_client.py) launches a local server process. Many modern
integrations are *hosted* instead, speaking the MCP "Streamable HTTP" transport:
JSON-RPC over an HTTP POST, with the reply coming back as either a JSON body or an
SSE (``text/event-stream``) frame. This module speaks that transport with the same
``start`` / ``call_tool`` / ``stop`` surface as the stdio client, so the rest of
ronin treats local and remote servers identically. The response parsing and
content flattening are pure and unit-tested.
"""
from __future__ import annotations

import json

PROTOCOL_VERSION = "2024-11-05"


def parse_rpc_response(content_type: str, body: str) -> dict:
    """Extract a JSON-RPC ``result`` from an HTTP reply that is either a JSON body
    or an SSE stream (``data: {...}`` lines). Raises on a JSON-RPC error. Pure."""
    text = (body or "").strip()
    is_sse = "text/event-stream" in (content_type or "").lower() or text.startswith("data:") \
        or "\ndata:" in text
    if is_sse:
        data_lines = [ln[len("data:"):].strip()
                      for ln in text.splitlines() if ln.startswith("data:")]
        # the last data frame carrying a JSON-RPC envelope is the response
        payload = ""
        for cand in reversed(data_lines):
            if cand and cand != "[DONE]":
                payload = cand
                break
        text = payload or "{}"
    msg = json.loads(text or "{}")
    if isinstance(msg, dict) and msg.get("error"):
        err = msg["error"]
        raise RuntimeError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
    return msg.get("result", {}) if isinstance(msg, dict) else {}


def stringify_content(result: dict) -> str:
    """Flatten an MCP tool-call result's ``content`` blocks to a string, matching
    the stdio client's behaviour. Pure."""
    parts = []
    for c in result.get("content", []):
        parts.append(c.get("text", "") if c.get("type") == "text" else json.dumps(c))
    text = "\n".join(p for p in parts if p).strip()
    return (f"ERROR: {text}" if result.get("isError") else text) or "(no output)"


class MCPRemoteClient:
    """A connection to one hosted MCP server over HTTP (Streamable HTTP)."""

    def __init__(self, name: str, url: str, headers: dict[str, str] | None = None,
                 timeout: float = 60.0) -> None:
        self.name = name
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout
        self.tools: list[dict] = []
        self._id = 0
        self._session_id: str | None = None

    # --- transport -----------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream", **self.headers}
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _rpc(self, method: str, params: dict, timeout: float | None = None) -> dict:
        import httpx
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        r = httpx.post(self.url, json=req, headers=self._headers(),
                       timeout=timeout or self.timeout)
        sid = r.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        r.raise_for_status()
        return parse_rpc_response(r.headers.get("content-type", ""), r.text)

    def _notify(self, method: str, params: dict | None = None) -> None:
        import httpx
        try:
            httpx.post(self.url, json={"jsonrpc": "2.0", "method": method, "params": params or {}},
                       headers=self._headers(), timeout=self.timeout)
        except Exception:  # noqa: BLE001 - notifications are best-effort
            pass

    # --- lifecycle (mirrors MCPClient) ---------------------------------------
    def start(self, timeout: float = 30.0) -> list[dict]:
        self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "ronin", "version": "0"},
        }, timeout)
        self._notify("notifications/initialized")
        self.tools = self._rpc("tools/list", {}, timeout).get("tools", [])
        return self.tools

    def stop(self) -> None:  # nothing to tear down for HTTP
        return None

    def call_tool(self, name: str, arguments: dict, timeout: float = 120.0) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments}, timeout)
        return stringify_content(result)
