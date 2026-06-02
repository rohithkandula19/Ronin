from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from ronin_cli.main import app


runner = CliRunner()


def test_plugins_no_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["plugins"])
    assert r.exit_code == 0
    assert "no plugin dir" in r.stdout.lower()


def test_plugins_lists_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    plugin_dir = tmp_path / ".csk" / "plugins"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "echo.py").write_text(dedent("""
        from ronin_agent_patterns import Tool
        def handler(text: str) -> str: return text
        def register_tools():
            return [Tool(name="echo", description="echo back",
                         input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                         handler=handler)]
    """))
    r = runner.invoke(app, ["plugins"])
    assert r.exit_code == 0
    assert "echo" in r.stdout
    assert "ok" in r.stdout


def test_costs_no_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["costs"])
    assert r.exit_code == 0
    assert "no usage recorded" in r.stdout.lower()


def test_costs_shows_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    from ronin_cli.usage import record_usage

    record_usage("ask", "anthropic", "claude-sonnet-4-6", 1000, 500)
    record_usage("ask", "anthropic", "claude-haiku-4-5", 5000, 2000)
    r = runner.invoke(app, ["costs"])
    assert r.exit_code == 0
    assert "claude-sonnet-4-6" in r.stdout
    assert "total calls" in r.stdout.lower()


def test_serve_without_auth_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = runner.invoke(app, ["serve", "--port", "8123"])
    assert r.exit_code == 2
    assert "ronin init" in r.stdout
