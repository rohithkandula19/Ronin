"""Tests for curl -> code conversion."""
from __future__ import annotations

from ronin_cli.curl_convert import parse_curl, to_javascript, to_python


def test_parse_simple_get() -> None:
    p = parse_curl("curl https://api.example.com/users")
    assert p["method"] == "GET" and p["url"] == "https://api.example.com/users"


def test_parse_post_with_json_and_headers() -> None:
    p = parse_curl('curl -X POST https://api.example.com -H "Content-Type: application/json" -d \'{"a":1}\'')
    assert p["method"] == "POST"
    assert p["headers"]["Content-Type"] == "application/json"
    assert p["data"] == '{"a":1}'


def test_data_implies_post() -> None:
    assert parse_curl("curl https://x.com -d 'hi'")["method"] == "POST"


def test_to_python_json_body() -> None:
    code = to_python(parse_curl('curl -X POST https://x.com -H "Authorization: Bearer t" -d \'{"k":"v"}\''))
    assert "import httpx" in code
    assert "json=payload" in code and '"k": "v"' in code
    assert "httpx.post(" in code


def test_to_javascript_fetch() -> None:
    code = to_javascript(parse_curl('curl -X PUT https://x.com -d \'{"k":1}\''))
    assert 'fetch("https://x.com"' in code
    assert '"method": "PUT"' in code and "Content-Type" in code
