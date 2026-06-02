from __future__ import annotations

from unittest.mock import MagicMock, patch

from ronin_cli import web_tools


def _resp(text: str, ctype: str = "text/html"):
    r = MagicMock()
    r.text = text
    r.headers = {"content-type": ctype}
    r.raise_for_status = MagicMock()
    return r


def test_web_search_parses_results() -> None:
    html = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">First Title</a>'
        '<div class="result__snippet">A snippet about the topic.</div>'
    )
    import httpx
    with patch.object(httpx, "post", return_value=_resp(html)):
        out = web_tools.web_search("topic")
    assert "First Title" in out
    assert "https://example.com/a" in out  # redirect unwrapped


def test_fetch_url_strips_html() -> None:
    import httpx
    page = "<html><head><style>x{}</style></head><body><h1>Hi</h1><script>bad()</script><p>Body text.</p></body></html>"
    with patch.object(httpx, "get", return_value=_resp(page)):
        out = web_tools.fetch_url("example.com")
    assert "Hi" in out and "Body text." in out
    assert "bad()" not in out and "<p>" not in out


def test_build_web_tools_shape() -> None:
    tools = web_tools.build_web_tools()
    names = {t.name for t in tools}
    assert names == {"web_search", "fetch_url"}
