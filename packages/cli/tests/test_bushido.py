"""Tests for Bushido — the global, cross-repo code of honor."""
from __future__ import annotations

from pathlib import Path

from ro_claude_kit_cli import bushido


def _home(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path / ".ronin"))
    return tmp_path / ".ronin" / "bushido.md"


def test_absent_returns_none(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path)
    assert bushido.load_bushido() is None
    assert bushido.bushido_system_block() is None


def test_remember_creates_and_loads(monkeypatch, tmp_path: Path) -> None:
    path = _home(monkeypatch, tmp_path)
    bushido.remember_rule("tests: pytest, never unittest")
    assert path.is_file()
    text = bushido.load_bushido()
    assert "pytest, never unittest" in text
    block = bushido.bushido_system_block()
    assert block is not None and "code of honor" in block.lower()
    assert "pytest, never unittest" in block


def test_remember_dedup(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path)
    bushido.remember_rule("prefer httpx over requests")
    bushido.remember_rule("- Prefer HTTPX over requests")  # same, diff case + dash
    text = bushido.load_bushido()
    assert text.lower().count("prefer httpx over requests") == 1


def test_remember_newest_first(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path)
    bushido.remember_rule("rule one")
    bushido.remember_rule("rule two")
    text = bushido.load_bushido()
    assert text.index("rule two") < text.index("rule one")


def test_tool_handler_persists(monkeypatch, tmp_path: Path) -> None:
    path = _home(monkeypatch, tmp_path)
    tool = bushido.build_bushido_tool()[0]
    assert tool.name == "remember_preference"
    out = tool.handler(rule="no comments unless asked")
    assert "code of honor" in out.lower()
    assert "no comments unless asked" in path.read_text(encoding="utf-8")
