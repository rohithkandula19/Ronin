"""Tests for User-Agent parsing."""
from __future__ import annotations

from ronin_cli.user_agent import parse_user_agent

_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_SAFARI_IOS = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
_FIREFOX_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0"
_EDGE = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 Edg/120.0.0.0"


def test_chrome_windows_desktop() -> None:
    r = parse_user_agent(_CHROME)
    assert r["browser"] == "Chrome" and r["version"].startswith("120")
    assert r["os"] == "Windows" and r["device"] == "desktop"


def test_safari_iphone_mobile() -> None:
    r = parse_user_agent(_SAFARI_IOS)
    assert r["browser"] == "Safari" and r["os"] == "iOS" and r["device"] == "mobile"


def test_firefox_mac() -> None:
    r = parse_user_agent(_FIREFOX_MAC)
    assert r["browser"] == "Firefox" and r["os"] == "macOS"


def test_edge_not_misdetected_as_chrome() -> None:
    assert parse_user_agent(_EDGE)["browser"] == "Edge"


def test_bot_and_tool() -> None:
    assert parse_user_agent("Googlebot/2.1 (+http://www.google.com/bot.html)")["device"] == "bot"
    assert parse_user_agent("curl/8.1.2")["browser"] == "curl"
