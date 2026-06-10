"""Vision-in-the-loop — let the coding agent SEE the UI it builds and self-correct.

A coding agent that writes a web UI is working blind: it can read the HTML/CSS
but not what the page actually looks like. These tools close that loop:

- ``screenshot`` renders a URL (typically a dev server the agent started with
  ``run_background``) to a PNG using headless Chrome.
- ``look_at`` asks a vision model what's in an image — reusing ronin's existing
  ``describe_image`` — so the agent can check "does this look right? any layout
  bugs?" and fix what it sees.

Both degrade gracefully: no browser → an actionable message; a text-only model →
the vision call's own error, with a hint to switch to a vision-capable model.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ronin_agent_patterns import Tool

from .config import RoninConfig
from .vision import describe_image

# Common headless-capable Chrome/Chromium locations, in priority order.
_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge",
)


def find_chrome() -> str | None:
    """Path to a headless-capable browser binary, or None."""
    for cand in _CHROME_CANDIDATES:
        if cand.startswith("/"):
            if Path(cand).is_file():
                return cand
        else:
            found = shutil.which(cand)
            if found:
                return found
    return None


def capture(url: str, out_path: Path, *, chrome: str | None = None,
            width: int = 1280, height: int = 900, wait_ms: int = 3000) -> None:
    """Render ``url`` to ``out_path`` (PNG) with headless Chrome. Raises if the
    browser is missing or the file isn't produced."""
    browser = chrome or find_chrome()
    if browser is None:
        raise RuntimeError("no headless browser found — install Google Chrome or Chromium")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--screenshot={out_path}", f"--window-size={width},{height}",
         f"--virtual-time-budget={wait_ms}", url],
        capture_output=True, timeout=45,
    )
    if not out_path.is_file():
        raise RuntimeError(f"screenshot failed — Chrome produced no file for {url}")


def build_vision_tools(config: RoninConfig, root: Path | str = ".") -> list[Tool]:
    root_path = Path(root).resolve()

    def _resolve(rel: str) -> Path:
        target = (root_path / rel).resolve()
        if root_path != target and root_path not in target.parents:
            raise ValueError(f"path {rel!r} escapes the project root")
        return target

    def screenshot(url: str, output: str = "screenshot.png") -> str:
        out = _resolve(output)
        try:
            capture(url, out)
        except Exception as e:  # noqa: BLE001
            return f"screenshot failed: {e}"
        size = out.stat().st_size
        rel = out.relative_to(root_path)
        return (f"saved screenshot of {url} → {rel} ({size // 1024} KB). "
                f"Call look_at(image='{rel}', question='…') to see what it looks like.")

    def look_at(image: str, question: str = "Describe this UI in detail. Note any "
                "layout problems, overlapping elements, broken styling, or obvious bugs.") -> str:
        target = _resolve(image)
        if not target.is_file():
            return f"no such image: {image}"
        try:
            return describe_image(config, target, question)
        except Exception as e:  # noqa: BLE001
            return (f"vision unavailable: {e} — this needs a vision-capable model; "
                    "try /login anthropic, or /model gpt-4o on OpenAI.")

    def _tool(name, desc, schema, handler) -> Tool:
        return Tool(name=name, description=desc, input_schema=schema, handler=handler)

    return [
        _tool("screenshot",
              "Render a URL to a PNG with a headless browser — use it to SEE a web UI you built "
              "(e.g. a dev server started with run_background: screenshot('http://localhost:3000')). "
              "Args: url, output (file path, default screenshot.png).",
              {"type": "object", "properties": {
                  "url": {"type": "string"}, "output": {"type": "string"}}, "required": ["url"]},
              screenshot),
        _tool("look_at",
              "Ask a vision model what's in a local image (a screenshot, mockup, or design). Use "
              "after screenshot to check the UI looks right and self-correct. Args: image (path), "
              "question (what to look for).",
              {"type": "object", "properties": {
                  "image": {"type": "string"}, "question": {"type": "string"}}, "required": ["image"]},
              look_at),
    ]
