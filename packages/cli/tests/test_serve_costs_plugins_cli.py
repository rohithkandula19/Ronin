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
    from ronin_cli.plugin_trust import trust

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RONIN_HOME", str(tmp_path / "_trust_home"))
    plugin_dir = tmp_path / ".ronin" / "plugins"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "echo.py").write_text(dedent("""
        from ronin_agent_patterns import Tool
        def handler(text: str) -> str: return text
        def register_tools():
            return [Tool(name="echo", description="echo back",
                         input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                         handler=handler)]
    """))
    # `ronin plugins` imports each plugin to read its tools, so it is an exec path
    # too: an untrusted file is listed but NOT imported. Trust it, as `plugin add`
    # would have. See test_plugin_trust.py.
    trust(plugin_dir / "echo.py")
    r = runner.invoke(app, ["plugins"])
    assert r.exit_code == 0
    assert "echo" in r.stdout
    assert "ok" in r.stdout


def test_plugins_lists_untrusted_without_importing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo-committed plugin is shown, flagged untrusted, and NOT executed."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RONIN_HOME", str(tmp_path / "_trust_home"))
    plugin_dir = tmp_path / ".ronin" / "plugins"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "evil.py").write_text(dedent("""
        from pathlib import Path
        Path(__file__).parent.joinpath("EXECUTED").write_text("ran")
        def register_tools(): return []
    """))
    r = runner.invoke(app, ["plugins"])
    assert r.exit_code == 0
    assert not (plugin_dir / "EXECUTED").exists(), "untrusted plugin was imported by `ronin plugins`"
    assert "untrusted" in r.stdout.lower()


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
    # Keep this a true no-auth test even when the developer has configured a
    # user-level Ronin provider locally.
    from ronin_cli import config as config_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_module, "USER_DIR", tmp_path / "user-config")
    monkeypatch.setattr(config_module, "LEGACY_USER_DIR", tmp_path / "legacy-user-config")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = runner.invoke(app, ["serve", "--port", "8123"])
    assert r.exit_code == 2
    assert "ronin init" in r.stdout
