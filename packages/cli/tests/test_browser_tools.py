"""Offline tests for the optional Playwright browser tools.

No real browser or network: Playwright is fully mocked. Covers
- graceful behavior when Playwright is absent (the real state in CI),
- each tool calling the right Playwright page methods (with a fake page),
- the agent being able to invoke a built tool,
- offline mode stripping the browser tools.
"""
from __future__ import annotations

import pytest

from ronin_cli import browser_tools


# --------------------------------------------------------------------------
# Fakes — a stand-in Playwright page that records the methods the tools call.
# --------------------------------------------------------------------------
class _FakeLocator:
    def __init__(self, page):
        self._page = page

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        self._page.calls.append(("text_click", None))


class _FakePage:
    def __init__(self):
        self.calls = []
        self.url = "https://example.com/"

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        self.calls.append(("goto", url))

    def title(self):
        return "Example Domain"

    def click(self, target, timeout=None):
        self.calls.append(("click", target))

    def get_by_text(self, text, exact=None):
        self.calls.append(("get_by_text", text))
        return _FakeLocator(self)

    def fill(self, selector, text, timeout=None):
        self.calls.append(("fill", (selector, text)))

    def inner_text(self, sel):
        self.calls.append(("inner_text", sel))
        return "Visible line one\n\nVisible line two"

    def screenshot(self, path=None):
        self.calls.append(("screenshot", path))
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG fake")


@pytest.fixture
def fake_page(monkeypatch):
    """Make the tools believe Playwright is installed and hand them a fake page."""
    page = _FakePage()
    monkeypatch.setattr(browser_tools, "playwright_available", lambda: True)
    monkeypatch.setattr(browser_tools._SESSION, "page", lambda: page)
    return page


# --------------------------------------------------------------------------
# Graceful behavior when Playwright is NOT installed (the real CI state).
# --------------------------------------------------------------------------
def test_not_available_returns_install_hint(monkeypatch):
    monkeypatch.setattr(browser_tools, "playwright_available", lambda: False)
    out = browser_tools.browse_navigate("example.com")
    assert out.startswith("ERROR")
    assert "pip install 'ronin-cli[browser]'" in out
    assert "playwright install chromium" in out


def test_build_returns_empty_when_absent(monkeypatch):
    monkeypatch.setattr(browser_tools, "playwright_available", lambda: False)
    assert browser_tools.build_browser_tools() == []
    assert browser_tools.browser_tools_available() is False


def test_install_hint_text():
    hint = browser_tools.install_hint()
    assert "pip install 'ronin-cli[browser]'" in hint
    assert "playwright install chromium" in hint


def test_missing_browser_binary_message(monkeypatch):
    monkeypatch.setattr(browser_tools, "playwright_available", lambda: True)

    def boom():
        raise RuntimeError("Executable doesn't exist at /x/chrome; run playwright install")

    monkeypatch.setattr(browser_tools._SESSION, "page", boom)
    out = browser_tools.browse_read()
    assert out.startswith("ERROR")
    assert "playwright install chromium" in out


# --------------------------------------------------------------------------
# Each tool calls the right Playwright page methods (with the fake page).
# --------------------------------------------------------------------------
def test_navigate_calls_goto_and_prefixes_scheme(fake_page):
    out = browser_tools.browse_navigate("example.com")
    assert ("goto", "https://example.com") in fake_page.calls
    assert "navigated to" in out and "Example Domain" in out


def test_click_uses_selector_first(fake_page):
    out = browser_tools.browse_click("button#go")
    assert ("click", "button#go") in fake_page.calls
    assert "clicked (selector)" in out


def test_click_falls_back_to_text(fake_page, monkeypatch):
    def fail_selector(target, timeout=None):
        raise RuntimeError("no such selector")

    monkeypatch.setattr(fake_page, "click", fail_selector)
    out = browser_tools.browse_click("Sign in")
    assert ("get_by_text", "Sign in") in fake_page.calls
    assert ("text_click", None) in fake_page.calls
    assert "clicked (text)" in out


def test_type_calls_fill(fake_page):
    out = browser_tools.browse_type("input#q", "hello world")
    assert ("fill", ("input#q", "hello world")) in fake_page.calls
    assert "typed into input#q" in out


def test_read_returns_inner_text(fake_page):
    out = browser_tools.browse_read()
    assert ("inner_text", "body") in fake_page.calls
    assert "Visible line one" in out and "Visible line two" in out


def test_read_truncates(fake_page, monkeypatch):
    monkeypatch.setattr(fake_page, "inner_text", lambda sel: "x " * 5000)
    out = browser_tools.browse_read(max_chars=100)
    assert "truncated" in out


def test_screenshot_writes_file(fake_page, tmp_path):
    target = tmp_path / "shots" / "page.png"
    out = browser_tools.browse_screenshot(str(target))
    assert target.is_file()
    assert ("screenshot", str(target)) in fake_page.calls
    assert "saved screenshot" in out


def test_page_action_error_is_returned_not_raised(fake_page, monkeypatch):
    def boom(url, wait_until=None, timeout=None):
        raise RuntimeError("navigation timeout")

    monkeypatch.setattr(fake_page, "goto", boom)
    out = browser_tools.browse_navigate("example.com")
    assert out.startswith("ERROR") and "navigation timeout" in out


# --------------------------------------------------------------------------
# Tool wiring — the agent can build and invoke a browser tool.
# --------------------------------------------------------------------------
def test_build_browser_tools_shape(monkeypatch):
    monkeypatch.setattr(browser_tools, "playwright_available", lambda: True)
    tools = {t.name: t for t in browser_tools.build_browser_tools()}
    for name in ("browse_navigate", "browse_click", "browse_type",
                 "browse_read", "browse_screenshot"):
        assert name in tools
    assert "url" in tools["browse_navigate"].input_schema["properties"]
    # write-ish actions are marked sensitive so the host approval gate prompts.
    assert tools["browse_navigate"].sensitive is True


def test_agent_can_invoke_built_tool(fake_page, monkeypatch):
    monkeypatch.setattr(browser_tools, "playwright_available", lambda: True)
    tools = {t.name: t for t in browser_tools.build_browser_tools()}
    # Invoke through the Tool's handler, the same path the agent uses.
    result = tools["browse_navigate"].handler(url="example.com")
    assert "navigated to" in result
    assert ("goto", "https://example.com") in fake_page.calls


def test_session_ensure_uses_sync_playwright(monkeypatch):
    """_Session._ensure imports playwright lazily and launches chromium headless."""
    import sys
    import types

    launched = {}

    class _FakeBrowser:
        def new_page(self):
            launched["new_page"] = True
            return _FakePage()

        def close(self):
            launched["closed"] = True

    class _FakeChromium:
        def launch(self, headless=None):
            launched["headless"] = headless
            return _FakeBrowser()

    class _FakePW:
        chromium = _FakeChromium()

        def stop(self):
            launched["stopped"] = True

    fake_mod = types.ModuleType("playwright.sync_api")
    fake_mod.sync_playwright = lambda: types.SimpleNamespace(start=lambda: _FakePW())
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_mod)

    sess = browser_tools._Session()
    page = sess._ensure()
    assert isinstance(page, _FakePage)
    assert launched["headless"] is True and launched["new_page"] is True
    sess.close()
    assert launched.get("closed") and launched.get("stopped")


# --------------------------------------------------------------------------
# Offline mode strips the browser tools.
# --------------------------------------------------------------------------
def test_offline_strips_browser_tools():
    from ronin_cli.offline import NETWORK_TOOLS, strip_network_tools

    class _T:
        def __init__(self, name):
            self.name = name

    for name in ("browse_navigate", "browse_click", "browse_type",
                 "browse_read", "browse_screenshot"):
        assert name in NETWORK_TOOLS
    kept = strip_network_tools([_T("read_file"), _T("browse_navigate"), _T("browse_read")])
    assert [t.name for t in kept] == ["read_file"]
