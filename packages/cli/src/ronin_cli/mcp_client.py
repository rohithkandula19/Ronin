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

# Operational environment variables an MCP subprocess legitimately needs to run
# (find its interpreter, its home, its temp dir, its locale). Everything OUTSIDE
# this set — every API key, token, and cloud credential in the parent shell — is
# withheld unless the server explicitly declares it needs it (``passEnv``). Before
# this, MCP servers were spawned with ``{**os.environ, ...}``, so an arbitrary
# ``npx -y <third-party-package>`` from the catalog inherited ANTHROPIC_API_KEY,
# AWS creds, GITHUB_TOKEN — every secret the user had exported.
_BASE_ENV_ALLOW = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD", "LANG", "LANGUAGE",
    "TZ", "TERM", "TMPDIR", "TEMP", "TMP", "COLORTERM", "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "SSL_CERT_FILE",
    "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    # Windows operational vars (os.environ upper-cases these on Windows)
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATHEXT", "COMSPEC", "APPDATA",
    "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "USERNAME", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
})


def _base_env() -> dict[str, str]:
    """The non-secret operational subset of the parent environment. Case-insensitive
    match on the name (Windows varies case), plus any ``LC_*`` locale var."""
    out: dict[str, str] = {}
    for k, v in os.environ.items():
        ku = k.upper()
        if ku in _BASE_ENV_ALLOW or ku.startswith("LC_"):
            out[k] = v
    return out


def scoped_env(pass_env: list[str] | None, explicit: dict[str, str] | None) -> dict[str, str]:
    """The environment an MCP subprocess is spawned with — NOT ``os.environ``.

    Three layers, least to most specific:

    1. :func:`_base_env` — operational vars only (PATH/HOME/temp/locale). No secrets.
    2. ``pass_env`` — parent vars the server DECLARED it needs (its catalog ``env``
       names, or ``passEnv`` in its config). Resolved from the parent at spawn time,
       so ``export GITHUB_TOKEN=… ; ronin`` still reaches the github server — the
       "set it later" UX ``mcp install`` documents — WITHOUT leaking anything the
       server didn't ask for. A declared-but-unset var is simply omitted.
    3. ``explicit`` — ``KEY=VALUE`` pairs written into the server's ``env`` block.
    """
    env = _base_env()
    for name in pass_env or []:
        if name in os.environ:
            env[name] = os.environ[name]
    if explicit:
        env.update({k: v for k, v in explicit.items() if v is not None})
    return env


def _catalog_pass_env(server_name: str) -> list[str]:
    """The env-var names a catalog server declares it needs — used to migrate
    servers installed before ``passEnv`` existed, so an old mcp.json entry for
    ``github`` still receives GITHUB_PERSONAL_ACCESS_TOKEN (and nothing else)."""
    try:
        from .mcp_catalog import resolve
        entry = resolve(server_name)
        return list(entry.env) if entry else []
    except Exception:  # noqa: BLE001
        return []


class MCPClient:
    """A live connection to one MCP server over stdio."""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None,
                 pass_env: list[str] | None = None) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        # Parent env-var names this server is allowed to inherit (see scoped_env).
        self.pass_env = pass_env or []
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
            # NOT {**os.environ}: a spawned server (often an arbitrary third-party
            # npx package) gets only operational vars + what it explicitly declared.
            text=True, bufsize=1, env=scoped_env(self.pass_env, self.env),
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


def add_mcp_server(name: str, command: str, args: list[str], root: str | Path = ".",
                   env: dict[str, str] | None = None,
                   pass_env: list[str] | None = None) -> Path:
    p = mcp_config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"mcpServers": {}}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data.setdefault("mcpServers", {})
        except ValueError:
            pass
    spec: dict = {"command": command, "args": args}
    if env:
        spec["env"] = env
    if pass_env:
        # Parent env-var names this server may inherit (resolved at spawn, so a key
        # exported after install is still picked up — without leaking every secret).
        spec["passEnv"] = list(pass_env)
    data["mcpServers"][name] = spec
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return p


def add_remote_mcp_server(name: str, url: str, root: str | Path = ".",
                          headers: dict[str, str] | None = None) -> Path:
    """Register a hosted (HTTP) MCP server by URL, with optional auth headers."""
    p = mcp_config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"mcpServers": {}}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data.setdefault("mcpServers", {})
        except ValueError:
            pass
    spec: dict = {"url": url}
    if headers:
        spec["headers"] = headers
    data["mcpServers"][name] = spec
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return p


def parse_env_pairs(pairs: list[str]) -> dict[str, str]:
    """Parse ``KEY=VALUE`` strings into a dict. A bare ``KEY`` (no ``=``) inherits
    the value from the current environment, so secrets needn't be typed. Pure-ish.

    Kept for callers that want eager resolution; new code should prefer
    :func:`split_env_spec`, which passes a bare ``KEY`` through by NAME (resolved at
    spawn) rather than baking its current value."""
    out: dict[str, str] = {}
    for item in pairs or []:
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v
        else:
            k = item.strip()
            if k:
                out[k] = os.environ.get(k, "")
    return out


def split_env_spec(pairs: list[str]) -> tuple[dict[str, str], list[str]]:
    """Split ``--env`` items into (explicit ``KEY=VALUE`` map, passthrough names).

    ``KEY=VALUE`` is a literal the user typed → stored in the server's ``env``.
    A bare ``KEY`` is a request to inherit that ONE parent var → a ``passEnv`` name,
    resolved at spawn (so a key exported later is still picked up, and an unset one
    is simply omitted rather than baked as an empty string that would shadow it)."""
    env_map: dict[str, str] = {}
    pass_env: list[str] = []
    for item in pairs or []:
        if "=" in item:
            k, v = item.split("=", 1)
            env_map[k.strip()] = v
        else:
            k = item.strip()
            if k:
                pass_env.append(k)
    return env_map, pass_env


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

    # ALL MCP tools are gated by default. We deliberately do NOT trust the
    # server-supplied annotations.readOnlyHint to auto-exempt a tool: a malicious
    # or typosquatted server could mark a destructive tool (delete_all,
    # transfer_funds) read-only to slip past the gate. The MCP spec itself warns
    # clients not to make trust decisions from untrusted-server annotations. The
    # one-time [a]lways answer handles read-only friction without trusting the server.
    return Tool(
        name=tool_name,
        description=f"[MCP:{client.name}] {spec.get('description', '')}".strip(),
        input_schema=spec.get("inputSchema") or {"type": "object", "properties": {}},
        handler=handler,
        sensitive=True,
    )


def build_mcp_tools(root: str | Path = ".", *, console=None) -> list:
    """Connect to every server in ``.ronin/mcp.json`` and return their tools as
    ronin Tools. Per-server failures are reported and skipped, never fatal."""
    tools: list = []
    for name, spec in load_mcp_servers(root).items():
        # Remote (hosted) servers carry a "url"; local ones carry a "command".
        if spec.get("url"):
            from .mcp_remote import MCPRemoteClient
            client = MCPRemoteClient(name, spec["url"], spec.get("headers"))
        else:
            command = spec.get("command")
            if not command:
                continue
            # A server declares which parent vars it may inherit via `passEnv`.
            # Old configs (pre-passEnv) fall back to the catalog's declared names,
            # so an existing `github` entry keeps working WITHOUT the old blanket
            # os.environ leak.
            pass_env = spec.get("passEnv") or _catalog_pass_env(name)
            client = MCPClient(name, command, spec.get("args"), spec.get("env"), pass_env)
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
