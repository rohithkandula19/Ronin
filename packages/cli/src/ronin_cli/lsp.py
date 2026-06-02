"""Semantic code intelligence via the Language Server Protocol.

The grep/text tools in ``code_tools`` are syntactic — they don't know that
``foo`` on line 10 is the same symbol as ``foo`` on line 200, or that a call
site resolves to a definition three files away. This module talks JSON-RPC over
stdio to a real language server (pyright, typescript-language-server, gopls,
rust-analyzer) to give the agent three *semantic* read-only tools:

- ``diagnostics(path)``  — type/lint errors for a file (the self-check the agent
  should run after editing, before declaring a task done).
- ``definition(path, symbol)`` — jump to where a symbol is defined.
- ``references(path, symbol)`` — every place a symbol is used.

Design principles, matching the rest of the kit:
- **Read-only.** None of these mutate anything, so they need no approval gate.
- **Graceful degradation.** If no server is installed for the file's language,
  the tool returns an actionable "install X" message instead of raising — the
  agent simply falls back to grep.
- **Ergonomic.** Tools take a *symbol name*, not a raw (line, character)
  position — the module locates the symbol in the file itself. LSP positions are
  an implementation detail the model never has to compute.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ronin_agent_patterns import Tool


# --------------------------------------------------------------------------- #
# Server registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LspServer:
    """How to launch + identify a language server for one family of files."""

    name: str
    command: list[str]
    language_id: str
    install_hint: str


# Extension → server. The command's first element is what we probe for on PATH.
SERVERS: dict[str, LspServer] = {
    ".py": LspServer("pyright", ["pyright-langserver", "--stdio"], "python",
                     "npm i -g pyright"),
    ".pyi": LspServer("pyright", ["pyright-langserver", "--stdio"], "python",
                      "npm i -g pyright"),
    ".ts": LspServer("typescript-language-server", ["typescript-language-server", "--stdio"],
                     "typescript", "npm i -g typescript typescript-language-server"),
    ".tsx": LspServer("typescript-language-server", ["typescript-language-server", "--stdio"],
                      "typescriptreact", "npm i -g typescript typescript-language-server"),
    ".js": LspServer("typescript-language-server", ["typescript-language-server", "--stdio"],
                     "javascript", "npm i -g typescript typescript-language-server"),
    ".jsx": LspServer("typescript-language-server", ["typescript-language-server", "--stdio"],
                      "javascriptreact", "npm i -g typescript typescript-language-server"),
    ".go": LspServer("gopls", ["gopls"], "go", "go install golang.org/x/tools/gopls@latest"),
    ".rs": LspServer("rust-analyzer", ["rust-analyzer"], "rust",
                     "rustup component add rust-analyzer"),
}


class LspUnavailable(Exception):
    """No language server is installed (or known) for a given file."""


def server_for(path: str | Path) -> LspServer:
    """Pick the server for ``path``'s extension, or raise ``LspUnavailable``."""
    ext = Path(path).suffix.lower()
    server = SERVERS.get(ext)
    if server is None:
        raise LspUnavailable(
            f"no language server configured for {ext or 'this file type'} "
            f"— LSP supports: {', '.join(sorted(SERVERS))}"
        )
    if shutil.which(server.command[0]) is None:
        raise LspUnavailable(
            f"{server.name} is not installed — install it with `{server.install_hint}`, "
            "then retry (or fall back to search_files)"
        )
    return server


# --------------------------------------------------------------------------- #
# JSON-RPC framing  (LSP uses HTTP-style Content-Length headers over stdio)
# --------------------------------------------------------------------------- #
def encode_message(payload: dict[str, Any]) -> bytes:
    """Frame a JSON-RPC payload as an LSP wire message."""
    body = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def read_message(stream) -> dict[str, Any] | None:
    """Read one framed JSON-RPC message from a binary stream.

    Returns the decoded payload, or ``None`` at clean EOF. Tolerates the extra
    headers some servers send (Content-Type) by reading until a blank line.
    """
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None  # EOF
        line = line.decode("ascii", errors="replace").strip()
        if line == "":
            break  # end of headers
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", 0))
    if length <= 0:
        return {}
    body = stream.read(length)
    return json.loads(body.decode("utf-8"))


# --------------------------------------------------------------------------- #
# Minimal synchronous LSP client
# --------------------------------------------------------------------------- #
class LspClient:
    """A small synchronous LSP client: one request at a time, plus collected
    ``publishDiagnostics`` notifications. Enough for definition / references /
    diagnostics — not a full async client."""

    def __init__(self, server: LspServer, root: Path):
        self.server = server
        self.root = root.resolve()
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self._diagnostics: dict[str, list[dict]] = {}
        self._stderr_thread: threading.Thread | None = None

    # ---- lifecycle ----
    def start(self) -> None:
        self._proc = subprocess.Popen(
            self.server.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.root),
        )
        # Drain stderr so a chatty server can't deadlock on a full pipe.
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        self._request("initialize", {
            "processId": None,
            "rootUri": self.root.as_uri(),
            "capabilities": {},
        })
        self._notify("initialized", {})

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._request("shutdown", None, timeout=2.0)
            self._notify("exit", None)
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=2.0)
        except Exception:
            self._proc.kill()
        self._proc = None

    def __enter__(self) -> "LspClient":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for _ in iter(proc.stderr.readline, b""):
                pass
        except Exception:
            pass

    # ---- wire ----
    def _send(self, payload: dict[str, Any]) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(encode_message(payload))
        self._proc.stdin.flush()

    def _notify(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: Any, *, timeout: float = 10.0) -> Any:
        """Send a request and pump messages until the matching response arrives,
        stashing any ``publishDiagnostics`` notifications seen on the way."""
        self._id += 1
        req_id = self._id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        assert self._proc and self._proc.stdout
        while True:
            msg = read_message(self._proc.stdout)
            if msg is None:
                raise LspUnavailable(f"{self.server.name} closed the connection")
            self._absorb_notification(msg)
            if msg.get("id") == req_id and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", "LSP error"))
                return msg.get("result")

    def _absorb_notification(self, msg: dict[str, Any]) -> None:
        if msg.get("method") == "textDocument/publishDiagnostics":
            params = msg.get("params", {})
            self._diagnostics[params.get("uri", "")] = params.get("diagnostics", [])

    def _pump_until_diagnostics(self, uri: str, *, settle_reads: int = 8) -> None:
        """Read a bounded number of messages so the server has a chance to emit
        diagnostics for ``uri`` after didOpen. Stops early once they arrive."""
        assert self._proc and self._proc.stdout
        for _ in range(settle_reads):
            if uri in self._diagnostics:
                return
            msg = read_message(self._proc.stdout)
            if msg is None:
                return
            self._absorb_notification(msg)

    # ---- documents ----
    def did_open(self, path: Path) -> str:
        uri = path.resolve().as_uri()
        text = path.read_text(encoding="utf-8", errors="replace")
        self._notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": self.server.language_id, "version": 1, "text": text,
        }})
        return uri

    def diagnostics(self, path: Path) -> list[dict]:
        uri = self.did_open(path)
        self._pump_until_diagnostics(uri)
        return self._diagnostics.get(uri, [])

    def definition(self, path: Path, position: dict) -> Any:
        uri = self.did_open(path)
        return self._request("textDocument/definition", {
            "textDocument": {"uri": uri}, "position": position,
        })

    def references(self, path: Path, position: dict) -> Any:
        uri = self.did_open(path)
        return self._request("textDocument/references", {
            "textDocument": {"uri": uri}, "position": position,
            "context": {"includeDeclaration": False},
        })


# --------------------------------------------------------------------------- #
# Helpers: symbol → position, location → human string, diagnostics formatting
# --------------------------------------------------------------------------- #
def locate_symbol(text: str, symbol: str) -> dict | None:
    """Return the 0-based {line, character} of the first occurrence of ``symbol``
    as a whole word, or ``None``. Whole-word so ``foo`` doesn't match ``foobar``."""
    import re

    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    for lineno, line in enumerate(text.splitlines()):
        m = pattern.search(line)
        if m:
            return {"line": lineno, "character": m.start()}
    return None


def _uri_to_rel(uri: str, root: Path) -> str:
    path = uri.removeprefix("file://")
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        return path


def format_locations(result: Any, root: Path) -> list[str]:
    """LSP definition/references return a Location | Location[] | null. Normalize
    to a list of ``file:line:col`` strings (1-based for humans)."""
    if not result:
        return []
    locations = result if isinstance(result, list) else [result]
    out: list[str] = []
    for loc in locations:
        uri = loc.get("uri") or loc.get("targetUri", "")
        rng = loc.get("range") or loc.get("targetSelectionRange") or {}
        start = rng.get("start", {})
        line = start.get("line", 0) + 1
        col = start.get("character", 0) + 1
        out.append(f"{_uri_to_rel(uri, root)}:{line}:{col}")
    return out


_SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}


def format_diagnostics(diags: list[dict]) -> str:
    if not diags:
        return "no diagnostics — file is clean ✓"
    lines: list[str] = []
    for d in sorted(diags, key=lambda x: x.get("range", {}).get("start", {}).get("line", 0)):
        sev = _SEVERITY.get(d.get("severity", 1), "error")
        start = d.get("range", {}).get("start", {})
        line = start.get("line", 0) + 1
        col = start.get("character", 0) + 1
        msg = d.get("message", "").strip().replace("\n", " ")
        source = d.get("source", "")
        tag = f"{source}: " if source else ""
        lines.append(f"  {sev} [{line}:{col}] {tag}{msg}")
    return f"{len(diags)} diagnostic(s):\n" + "\n".join(lines)


# --------------------------------------------------------------------------- #
# Tool factory
# --------------------------------------------------------------------------- #
def build_lsp_tools(root: Path | str = ".") -> list[Tool]:
    """Three read-only semantic tools. Each spins up a short-lived language
    server for the target file, queries it, and shuts it down — stateless from
    the agent's point of view, and free of any long-lived process to manage."""
    root_path = Path(root).resolve()

    def _resolve(rel: str) -> Path:
        target = (root_path / rel).resolve()
        if root_path != target and root_path not in target.parents:
            raise ValueError(f"path {rel!r} escapes the project root")
        return target

    def diagnostics(path: str) -> str:
        try:
            server = server_for(path)
        except LspUnavailable as e:
            return f"LSP unavailable: {e}"
        target = _resolve(path)
        if not target.is_file():
            return f"ERROR: {path} is not a file"
        try:
            with LspClient(server, root_path) as client:
                return format_diagnostics(client.diagnostics(target))
        except Exception as e:  # noqa: BLE001 — never let LSP crash a turn
            return f"LSP error ({server.name}): {e}"

    def definition(path: str, symbol: str) -> str:
        try:
            server = server_for(path)
        except LspUnavailable as e:
            return f"LSP unavailable: {e}"
        target = _resolve(path)
        if not target.is_file():
            return f"ERROR: {path} is not a file"
        pos = locate_symbol(target.read_text(encoding="utf-8", errors="replace"), symbol)
        if pos is None:
            return f"symbol {symbol!r} not found in {path}"
        try:
            with LspClient(server, root_path) as client:
                locs = format_locations(client.definition(target, pos), root_path)
        except Exception as e:  # noqa: BLE001
            return f"LSP error ({server.name}): {e}"
        if not locs:
            return f"no definition found for {symbol!r}"
        return f"{symbol} is defined at:\n" + "\n".join(f"  {l}" for l in locs)

    def references(path: str, symbol: str) -> str:
        try:
            server = server_for(path)
        except LspUnavailable as e:
            return f"LSP unavailable: {e}"
        target = _resolve(path)
        if not target.is_file():
            return f"ERROR: {path} is not a file"
        pos = locate_symbol(target.read_text(encoding="utf-8", errors="replace"), symbol)
        if pos is None:
            return f"symbol {symbol!r} not found in {path}"
        try:
            with LspClient(server, root_path) as client:
                locs = format_locations(client.references(target, pos), root_path)
        except Exception as e:  # noqa: BLE001
            return f"LSP error ({server.name}): {e}"
        if not locs:
            return f"no references found for {symbol!r}"
        return f"{len(locs)} reference(s) to {symbol}:\n" + "\n".join(f"  {l}" for l in locs)

    def _tool(name, desc, schema, handler) -> Tool:
        return Tool(name=name, description=desc, input_schema=schema, handler=handler)

    _path_symbol = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to project root."},
            "symbol": {"type": "string", "description": "Symbol name to resolve (whole-word, first occurrence)."},
        },
        "required": ["path", "symbol"],
    }

    return [
        _tool("diagnostics",
              "Type-check / lint a file via its language server and return errors + warnings "
              "(line, severity, message). Run this after editing to verify your change compiles "
              "cleanly before declaring a task done. Read-only. Falls back gracefully if no "
              "language server is installed.",
              {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
              diagnostics),
        _tool("definition",
              "Go to the definition of a symbol — semantic, not grep. Returns file:line:col where "
              "the symbol is defined (possibly in another file). Read-only.",
              _path_symbol, definition),
        _tool("references",
              "Find every reference to a symbol across the project — semantic, not grep. Returns "
              "a list of file:line:col use sites. Read-only.",
              _path_symbol, references),
    ]
