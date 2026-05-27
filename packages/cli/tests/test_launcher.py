from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ro_claude_kit_cli.main import app


def test_bare_ronin_non_interactive_shows_help() -> None:
    """`ronin` with no args, non-interactive (piped/CI), prints help and exits 0
    instead of trying to open an interactive session."""
    r = CliRunner().invoke(app, [])
    assert r.exit_code == 0
    assert "Usage" in r.output or "Commands" in r.output


def test_bare_ronin_interactive_opens_unified_session(monkeypatch, tmp_path) -> None:
    """`ronin` with no args, interactive + authed → drops into the unified session."""
    import sys as _sys
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_sys.stdout, "isatty", lambda: True)
    calls = {}
    with patch("ro_claude_kit_cli.panda_art.render_panda"), \
         patch("ro_claude_kit_cli.code_mode.run_unified_session",
               side_effect=lambda c, **k: calls.update(root=str(k.get("root")))):
        from ro_claude_kit_cli.main import _root
        ctx = MagicMock()
        ctx.invoked_subcommand = None
        _root(ctx)
    assert "root" in calls
