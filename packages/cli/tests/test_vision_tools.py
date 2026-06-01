"""Tests for vision-in-the-loop tools (screenshot + look_at). Chrome and the
vision model are mocked so the tests are deterministic and offline."""
from __future__ import annotations

from pathlib import Path

from ro_claude_kit_cli import vision_tools
from ro_claude_kit_cli.config import CSKConfig


def _cfg() -> CSKConfig:
    return CSKConfig(provider="anthropic", anthropic_api_key="sk-x")


def _tools(root):
    return {t.name: t for t in vision_tools.build_vision_tools(_cfg(), root)}


def test_find_chrome_detects_path(monkeypatch) -> None:
    monkeypatch.setattr(vision_tools.Path, "is_file", lambda self: True)
    assert vision_tools.find_chrome() is not None


def test_find_chrome_none_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(vision_tools.Path, "is_file", lambda self: False)
    monkeypatch.setattr(vision_tools.shutil, "which", lambda _c: None)
    assert vision_tools.find_chrome() is None


def test_screenshot_tool_success(monkeypatch, tmp_path) -> None:
    # fake capture: just create the output file
    def fake_capture(url, out_path, **kw):
        out_path.write_bytes(b"\x89PNG fake image data")
    monkeypatch.setattr(vision_tools, "capture", fake_capture)

    out = _tools(tmp_path)["screenshot"].handler(url="http://localhost:3000", output="shot.png")
    assert "saved screenshot" in out
    assert (tmp_path / "shot.png").exists()


def test_screenshot_tool_no_browser(monkeypatch, tmp_path) -> None:
    def boom(url, out_path, **kw):
        raise RuntimeError("no headless browser found — install Google Chrome")
    monkeypatch.setattr(vision_tools, "capture", boom)
    out = _tools(tmp_path)["screenshot"].handler(url="http://x")
    assert "screenshot failed" in out and "Chrome" in out


def test_look_at_tool_passes_through(monkeypatch, tmp_path) -> None:
    (tmp_path / "ui.png").write_bytes(b"img")
    monkeypatch.setattr(vision_tools, "describe_image",
                        lambda config, path, question: "A clean login form, centered.")
    out = _tools(tmp_path)["look_at"].handler(image="ui.png", question="how does it look?")
    assert "clean login form" in out


def test_look_at_missing_image(tmp_path) -> None:
    out = _tools(tmp_path)["look_at"].handler(image="nope.png")
    assert "no such image" in out


def test_look_at_vision_unavailable(monkeypatch, tmp_path) -> None:
    (tmp_path / "ui.png").write_bytes(b"img")
    def boom(config, path, question):
        raise RuntimeError("model is text-only")
    monkeypatch.setattr(vision_tools, "describe_image", boom)
    out = _tools(tmp_path)["look_at"].handler(image="ui.png")
    assert "vision unavailable" in out and "vision-capable model" in out


def test_screenshot_refuses_path_escape(tmp_path) -> None:
    import pytest
    with pytest.raises(ValueError, match="escapes the project root"):
        _tools(tmp_path)["screenshot"].handler(url="http://x", output="../../etc/evil.png")
