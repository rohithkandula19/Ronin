"""Coverage for web_tools helpers + tool handlers (mocked network)."""
from __future__ import annotations

import pytest

from ronin_cli import web_tools
from ronin_cli.web_tools import _ddg_unwrap, _strip_tags, build_web_tools


def test_strip_tags_removes_markup_and_unescapes() -> None:
    assert _strip_tags("<b>hello</b> &amp; bye") == "hello & bye"
    assert _strip_tags("<a href='x'>link</a>") == "link"


def test_strip_tags_empty() -> None:
    assert _strip_tags("") == ""


def test_ddg_unwrap_extracts_target() -> None:
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=abc"
    assert _ddg_unwrap(wrapped) == "https://example.com/page"


def test_ddg_unwrap_passthrough_http() -> None:
    assert _ddg_unwrap("https://already.com") == "https://already.com"


def test_ddg_unwrap_protocol_relative() -> None:
    assert _ddg_unwrap("//example.com/x").startswith("https:")


def test_build_web_tools_shape() -> None:
    tools = {t.name: t for t in build_web_tools()}
    assert "web_search" in tools and "fetch_url" in tools
    assert "query" in tools["web_search"].input_schema["properties"]


class _Resp:
    def __init__(self, text="", status=200, ctype="text/html"):
        self.text = text
        self.status_code = status
        self.headers = {"content-type": ctype}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")


def test_fetch_url_strips_html(monkeypatch) -> None:
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp("<html><body>Hello <b>world</b></body></html>"))
    out = web_tools.fetch_url("example.com")
    assert "Hello" in out and "world" in out and "<b>" not in out


def test_fetch_url_handles_error(monkeypatch) -> None:
    import httpx
    def boom(*a, **k):
        raise httpx.ConnectError("no network")
    monkeypatch.setattr(httpx, "get", boom)
    assert web_tools.fetch_url("example.com").startswith("ERROR")


def test_web_search_no_results(monkeypatch) -> None:
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp("<html>nothing</html>"))
    assert "No results" in web_tools.web_search("zxcvbnm")
