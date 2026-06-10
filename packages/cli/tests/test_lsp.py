"""Tests for the LSP semantic-intelligence layer.

No real language server is required: the JSON-RPC client is exercised against a
fake in-memory subprocess whose stdout is pre-framed with canned responses, so
the request/response correlation and document flow are covered deterministically
and offline (matching the kit's FakeProvider philosophy)."""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from ronin_cli import lsp


# ---------- framing ----------


def test_encode_read_roundtrip() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "x", "params": {"a": 1}}
    wire = lsp.encode_message(payload)
    assert wire.startswith(b"Content-Length: ")
    back = lsp.read_message(io.BytesIO(wire))
    assert back == payload


def test_read_message_eof_returns_none() -> None:
    assert lsp.read_message(io.BytesIO(b"")) is None


def test_read_message_tolerates_extra_headers() -> None:
    body = b'{"ok": true}'
    wire = (b"Content-Length: " + str(len(body)).encode() +
            b"\r\nContent-Type: application/vscode-jsonrpc\r\n\r\n" + body)
    assert lsp.read_message(io.BytesIO(wire)) == {"ok": True}


# ---------- server registry ----------


def test_server_for_unknown_extension_raises() -> None:
    with pytest.raises(lsp.LspUnavailable, match="no language server"):
        lsp.server_for("notes.txt")


def test_server_for_missing_binary_raises_with_install_hint(monkeypatch) -> None:
    monkeypatch.setattr(lsp.shutil, "which", lambda _cmd: None)
    with pytest.raises(lsp.LspUnavailable, match="npm i -g pyright"):
        lsp.server_for("main.py")


def test_server_for_installed_returns_server(monkeypatch) -> None:
    monkeypatch.setattr(lsp.shutil, "which", lambda _cmd: "/usr/bin/" + _cmd)
    server = lsp.server_for("app.tsx")
    assert server.name == "typescript-language-server"
    assert server.language_id == "typescriptreact"


# ---------- symbol location ----------


def test_locate_symbol_whole_word() -> None:
    text = "x = foobar\nresult = foo(1)\n"
    pos = lsp.locate_symbol(text, "foo")
    # 'foobar' on line 0 must NOT match; 'foo' is on line 1
    assert pos == {"line": 1, "character": 9}


def test_locate_symbol_missing_returns_none() -> None:
    assert lsp.locate_symbol("a = 1\n", "zzz") is None


# ---------- formatting ----------


def test_format_locations_handles_single_and_list_and_link(tmp_path) -> None:
    root = tmp_path
    uri = (root / "src" / "m.py").as_uri()
    single = {"uri": uri, "range": {"start": {"line": 4, "character": 2}}}
    assert lsp.format_locations(single, root) == ["src/m.py:5:3"]
    assert lsp.format_locations([single, single], root) == ["src/m.py:5:3", "src/m.py:5:3"]
    # LocationLink shape (targetUri / targetSelectionRange)
    link = {"targetUri": uri, "targetSelectionRange": {"start": {"line": 0, "character": 0}}}
    assert lsp.format_locations(link, root) == ["src/m.py:1:1"]


def test_format_locations_empty() -> None:
    assert lsp.format_locations(None, Path(".")) == []


def test_format_diagnostics_clean_and_populated() -> None:
    assert "clean" in lsp.format_diagnostics([])
    diags = [
        {"severity": 1, "range": {"start": {"line": 9, "character": 4}},
         "message": "Undefined name 'x'", "source": "pyright"},
        {"severity": 2, "range": {"start": {"line": 1, "character": 0}},
         "message": "Unused import"},
    ]
    out = lsp.format_diagnostics(diags)
    assert "2 diagnostic(s)" in out
    # sorted by line → warning on line 2 comes before error on line 10
    assert out.index("Unused import") < out.index("Undefined name")
    assert "error [10:5] pyright: Undefined name" in out


# ---------- full client cycle against a fake server ----------


class _FakeProc:
    """A stand-in for subprocess.Popen whose stdout is pre-framed bytes."""

    def __init__(self, responses: bytes):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(responses)
        self.stderr = io.BytesIO(b"")

    def terminate(self) -> None: ...
    def wait(self, timeout=None) -> int: return 0
    def kill(self) -> None: ...


def _framed(*payloads: dict) -> bytes:
    return b"".join(lsp.encode_message(p) for p in payloads)


def test_client_definition_cycle(monkeypatch, tmp_path) -> None:
    src = tmp_path / "m.py"
    src.write_text("def foo():\n    pass\n\nfoo()\n", encoding="utf-8")
    uri = src.as_uri()

    # id=1 → initialize result; id=2 → definition result pointing at line 0
    responses = _framed(
        {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "result": {
            "uri": uri, "range": {"start": {"line": 0, "character": 4}}}},
    )
    monkeypatch.setattr(lsp.subprocess, "Popen", lambda *a, **k: _FakeProc(responses))
    monkeypatch.setattr(lsp.shutil, "which", lambda _cmd: "/usr/bin/pyright-langserver")

    server = lsp.server_for(str(src))
    with lsp.LspClient(server, tmp_path) as client:
        pos = lsp.locate_symbol(src.read_text(), "foo")
        result = client.definition(src, pos)
    assert lsp.format_locations(result, tmp_path) == ["m.py:1:5"]


def test_client_diagnostics_cycle(monkeypatch, tmp_path) -> None:
    src = tmp_path / "bad.py"
    src.write_text("x: int = 'oops'\n", encoding="utf-8")
    uri = src.as_uri()

    responses = _framed(
        {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
        {"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {
            "uri": uri, "diagnostics": [
                {"severity": 1, "range": {"start": {"line": 0, "character": 9}},
                 "message": "Type mismatch", "source": "pyright"}]}},
    )
    monkeypatch.setattr(lsp.subprocess, "Popen", lambda *a, **k: _FakeProc(responses))
    monkeypatch.setattr(lsp.shutil, "which", lambda _cmd: "/usr/bin/pyright-langserver")

    server = lsp.server_for(str(src))
    with lsp.LspClient(server, tmp_path) as client:
        diags = client.diagnostics(src)
    assert lsp.format_diagnostics(diags) == (
        "1 diagnostic(s):\n  error [1:10] pyright: Type mismatch")


# ---------- tool factory + graceful degradation ----------


def test_lsp_tools_built() -> None:
    tools = lsp.build_lsp_tools(".")
    assert {t.name for t in tools} == {"diagnostics", "definition", "references"}


def test_diagnostics_tool_degrades_when_unavailable(monkeypatch, tmp_path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(lsp.shutil, "which", lambda _cmd: None)  # nothing installed
    tools = {t.name: t for t in lsp.build_lsp_tools(tmp_path)}
    out = tools["diagnostics"].handler(path="a.py")
    assert "LSP unavailable" in out and "npm i -g pyright" in out


def test_definition_tool_reports_missing_symbol(monkeypatch, tmp_path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(lsp.shutil, "which", lambda _cmd: "/usr/bin/pyright-langserver")
    tools = {t.name: t for t in lsp.build_lsp_tools(tmp_path)}
    out = tools["definition"].handler(path="a.py", symbol="nonexistent")
    assert "not found" in out


def test_lsp_tool_refuses_path_escape(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(lsp.shutil, "which", lambda _cmd: "/usr/bin/pyright-langserver")
    tools = {t.name: t for t in lsp.build_lsp_tools(tmp_path)}
    # server_for passes (ext known + "installed"), then _resolve rejects the escape
    with pytest.raises(ValueError, match="escapes the project root"):
        tools["diagnostics"].handler(path="../../etc/passwd.py")
