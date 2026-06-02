"""Minimal MCP (Model Context Protocol) stdio client.

Lets ronin use tools from any MCP server — Anthropic's open protocol — e.g. the
filesystem, GitHub, or Slack servers. Servers are declared in ``.ronin/mcp.json``
using Claude Code's ``mcpServers`` shape:

    {
      "mcpServers": {
        "fs": {"command": "npx",
               "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"]}
      }
    }

Each server is spawned as a subprocess; we speak JSON-RPC 2.0 over its stdin/
stdout (newline-delimited), do the ``initialize`` → ``tools/list`` handshake, and
wrap every discovered tool as a ronin ``Tool`` the agent can call.
"""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import threading
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"
_ACTIVE: list["MCPClient"] = []


class MCPClient:
    """A live connection to one MCP server over stdio."""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self._id = 0
        self._pending: dict[int, tuple[threading.Event, dict]] = {}
        self._lock = threading.Lock()

    # --- lifecycle -----------------------------------------------------------
    def start(self, timeout: float = 30.0) -> list[dict]:
        self.proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env={**os.environ, **self.env},
        )
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "ronin", "version": "0"},
        }, timeout)
        self._notify("notifications/initialized")
        self.tools = self._request("tools/list", {}, timeout).get("tools", [])
        return self.tools

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    # --- JSON-RPC ------------------------------------------------------------
    def _read_loop(self) -> None:
        try:
            assert self.proc and self.proc.stdout
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mid = msg.get("id")
                if mid in self._pending:
                    ev, holder = self._pending[mid]
                    holder["msg"] = msg
                    ev.set()
        except Exception:  # noqa: BLE001 - server died; pending waiters will time out
            pass

    def _send(self, obj: dict) -> None:
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            raise RuntimeError(f"MCP server '{self.name}' is not running")
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        with self._lock:
            self._id += 1
            mid = self._id
        ev, holder = threading.Event(), {}
        self._pending[mid] = (ev, holder)
        try:
            self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
            if not ev.wait(timeout):
                raise TimeoutError(f"MCP '{self.name}' {method} timed out after {timeout}s")
            msg = holder["msg"]
        finally:
            self._pending.pop(mid, None)
        if "error" in msg:
            raise RuntimeError(f"MCP '{self.name}' {method}: {msg['error']}")
        return msg.get("result", {})

    def _notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call_tool(self, name: str, arguments: dict, timeout: float = 120.0) -> str:
        res = self._request("tools/call", {"name": name, "arguments": arguments}, timeout)
        parts = []
        for c in res.get("content", []):
            parts.append(c.get("text", "") if c.get("type") == "text" else json.dumps(c))
        text = "\n".join(p for p in parts if p).strip()
        return (f"ERROR: {text}" if res.get("isError") else text) or "(no output)"


# --- config + tool wiring ----------------------------------------------------
def mcp_config_path(root: str | Path = ".") -> Path:
    return Path(root) / ".ronin" / "mcp.json"


def load_mcp_servers(root: str | Path = ".") -> dict:
    p = mcp_config_path(root)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("mcpServers", {})
    except (OSError, ValueError):
        return {}


def add_mcp_server(name: str, command: str, args: list[str], root: str | Path = ".") -> Path:
    p = mcp_config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"mcpServers": {}}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data.setdefault("mcpServers", {})
        except ValueError:
            pass
    data["mcpServers"][name] = {"command": command, "args": args}
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return p


def remove_mcp_server(name: str, root: str | Path = ".") -> bool:
    """Remove a server from ``.ronin/mcp.json``. Returns True if it was present."""
    p = mcp_config_path(root)
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return False
    servers = data.get("mcpServers", {})
    if name not in servers:
        return False
    del servers[name]
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def _wrap_tool(client: MCPClient, spec: dict):
    from ronin_agent_patterns import Tool

    tool_name = f"{client.name}__{spec['name']}"

    def handler(**kwargs) -> str:
        try:
            return client.call_tool(spec["name"], kwargs)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {e}"

    return Tool(
        name=tool_name,
        description=f"[MCP:{client.name}] {spec.get('description', '')}".strip(),
        input_schema=spec.get("inputSchema") or {"type": "object", "properties": {}},
        handler=handler,
    )


def build_mcp_tools(root: str | Path = ".", *, console=None) -> list:
    """Connect to every server in ``.ronin/mcp.json`` and return their tools as
    ronin Tools. Per-server failures are reported and skipped, never fatal."""
    tools: list = []
    for name, spec in load_mcp_servers(root).items():
        command = spec.get("command")
        if not command:
            continue
        client = MCPClient(name, command, spec.get("args"), spec.get("env"))
        try:
            discovered = client.start()
        except Exception as e:  # noqa: BLE001
            if console:
                console.print(f"[yellow]⚠ MCP '{name}' failed to start: {e}[/yellow]")
            client.stop()
            continue
        _ACTIVE.append(client)
        atexit.register(client.stop)
        tools.extend(_wrap_tool(client, t) for t in discovered)
        if console:
            console.print(f"[#6b7089]🔌 MCP [bold]{name}[/bold] · {len(discovered)} tool(s)[/#6b7089]")
    return tools
