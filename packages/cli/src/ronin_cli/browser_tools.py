"""Optional web computer-use for the agent via Playwright — free, no vision model.

This is an OPTIONAL extra. Playwright is NOT a core dependency: it is imported
LAZILY inside each function, and the tools fail gracefully with a clear install
hint when it (or a browser binary) is missing. Core ronin stays offline and
installs fine without this.

Reading the page uses the DOM / accessibility tree (browse_read), so no vision
model is needed to drive the web. The agent gets five tools:

- ``browse_navigate(url)``   open a URL in a real Chromium page.
- ``browse_click(target)``   click by CSS selector or by visible text.
- ``browse_type(selector, text)``  type text into an input.
- ``browse_read()``          return the page's visible text (the DOM, cheap).
- ``browse_screenshot(path)`` save a PNG of the current page.

Install to enable:  pip install 'ronin-cli[browser]' && playwright install chromium
"""
from __future__ import annotations

import threading
from pathlib import Path

# One install line, reused by the hint and the tool error messages.
INSTALL_HINT = (
    "Playwright browser tools are not installed. Enable them with:\n"
    "  pip install 'ronin-cli[browser]'\n"
    "  playwright install chromium\n"
    "(this is an optional extra; core ronin does not need it)."
)


def playwright_available() -> bool:
    """True if the ``playwright`` package is importable (not whether a browser exists)."""
    import importlib.util

    return importlib.util.find_spec("playwright") is not None


def install_hint() -> str:
    """Clear, copy-pasteable instructions to enable the browser tools."""
    return INSTALL_HINT


def browser_tools_available() -> bool:
    """Gate for wiring: only expose the tools when Playwright is importable.

    A missing browser binary is reported per-call (with the install hint) rather
    than hiding the tools, so the agent learns exactly what to run.
    """
    return playwright_available()


class _Session:
    """Lazily-started Playwright Chromium page, reused across tool calls.

    Holds the playwright handle, browser, and a single page. Started on first
    use; closed by ``close()``. Thread-guarded so concurrent tool calls don't
    race the startup. All heavy imports happen inside ``_ensure``.
    """

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._page = None
        self._lock = threading.Lock()

    def _ensure(self):
        """Return a live page, starting the browser if needed. Raises on failure."""
        with self._lock:
            if self._page is not None:
                return self._page
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            # headless=True: no window, works in CI/servers. chromium is the
            # one we tell users to `playwright install`.
            self._browser = self._pw.chromium.launch(headless=True)
            self._page = self._browser.new_page()
            return self._page

    def page(self):
        return self._ensure()

    def close(self) -> None:
        """Tear down the page/browser/playwright handle. Safe to call twice."""
        with self._lock:
            for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
                if obj is not None:
                    try:
                        getattr(obj, meth)()
                    except Exception:  # noqa: BLE001 — best-effort teardown
                        pass
            self._pw = self._browser = self._page = None


# Module-level singleton so the agent drives one browser across tool calls.
_SESSION = _Session()


def _missing_browser_msg(err: Exception) -> str:
    """Map a Playwright launch error to an actionable message."""
    text = str(err)
    if "Executable doesn't exist" in text or "playwright install" in text:
        return ("ERROR: no Chromium browser installed for Playwright. Run:\n"
                "  playwright install chromium")
    return f"ERROR: could not start the browser: {text}"


def _with_page(fn):
    """Run ``fn(page)`` guarding the two failure modes: no Playwright, no browser."""
    if not playwright_available():
        return "ERROR: " + INSTALL_HINT
    try:
        page = _SESSION.page()
    except Exception as e:  # noqa: BLE001 — launch failure (usually missing browser)
        return _missing_browser_msg(e)
    try:
        return fn(page)
    except Exception as e:  # noqa: BLE001 — page action failure, hand back to the agent
        return f"ERROR: {e}"


def browse_navigate(url: str) -> str:
    """Open ``url`` in the browser. Returns the final URL and page title."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    def _go(page):
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return f"navigated to {page.url}\ntitle: {page.title()}"

    return _with_page(_go)


def browse_click(target: str) -> str:
    """Click an element. ``target`` is a CSS selector, or visible text to match."""

    def _click(page):
        try:
            page.click(target, timeout=8000)
            how = "selector"
        except Exception:  # noqa: BLE001 — fall back to a text match
            page.get_by_text(target, exact=False).first.click(timeout=8000)
            how = "text"
        return f"clicked ({how}): {target}\nnow at {page.url}"

    return _with_page(_click)


def browse_type(selector: str, text: str) -> str:
    """Type ``text`` into the element matched by ``selector`` (a CSS selector)."""

    def _type(page):
        page.fill(selector, text, timeout=8000)
        return f"typed into {selector}: {text[:80]}"

    return _with_page(_type)


def browse_read(max_chars: int = 6000) -> str:
    """Return the page's visible text (DOM innerText) — no vision model needed."""

    def _read(page):
        text = page.inner_text("body")
        text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
        clipped = text[:max_chars]
        if len(text) > max_chars:
            clipped += f"\n...(truncated, {len(text)} chars total)"
        return clipped or "(no visible text on page)"

    return _with_page(_read)


def browse_screenshot(path: str = "page.png") -> str:
    """Save a PNG of the current page to ``path``."""

    def _shot(page):
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out))
        return f"saved screenshot -> {out} ({out.stat().st_size // 1024} KB)"

    return _with_page(_shot)


def browse_close() -> str:
    """Close the browser and free its resources."""
    _SESSION.close()
    return "browser closed"


def build_browser_tools() -> list:
    """Playwright browser tools for the agent's tool list.

    Returns an empty list when Playwright is not installed, so wiring this in
    never breaks the toolbelt — the feature is simply absent until enabled.
    """
    if not browser_tools_available():
        return []
    from ronin_agent_patterns import Tool

    def _tool(name, desc, schema, handler, *, sensitive=False) -> "Tool":
        return Tool(name=name, description=desc, input_schema=schema,
                    handler=handler, sensitive=sensitive)

    return [
        _tool(
            "browse_navigate",
            "Open a URL in a real browser (Playwright/Chromium). Use to drive a web "
            "page: navigate, then browse_read to see it, browse_click / browse_type to act.",
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            browse_navigate, sensitive=True,
        ),
        _tool(
            "browse_click",
            "Click an element on the current page. target is a CSS selector (e.g. "
            "'button#go') or visible text (e.g. 'Sign in'). Navigate first.",
            {"type": "object", "properties": {"target": {"type": "string"}},
             "required": ["target"]},
            browse_click, sensitive=True,
        ),
        _tool(
            "browse_type",
            "Type text into an input on the current page. selector is a CSS selector "
            "for the field; text is what to type.",
            {"type": "object", "properties": {
                "selector": {"type": "string"}, "text": {"type": "string"}},
             "required": ["selector", "text"]},
            browse_type, sensitive=True,
        ),
        _tool(
            "browse_read",
            "Return the current page's visible text (the DOM, no vision model needed). "
            "Use to read a page after browse_navigate or an action.",
            {"type": "object", "properties": {
                "max_chars": {"type": "integer", "description": "default 6000"}}},
            browse_read,
        ),
        _tool(
            "browse_screenshot",
            "Save a PNG screenshot of the current page to a path (default page.png).",
            {"type": "object", "properties": {"path": {"type": "string"}}},
            browse_screenshot, sensitive=True,
        ),
    ]
