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


def test_bare_ronin_interactive_opens_tui_by_default(monkeypatch, tmp_path) -> None:
    """`ronin` with no args, interactive + authed → opens the full-screen TUI by
    default (the inline REPL is the `--no-tui` opt-out)."""
    import sys as _sys
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_sys.stdout, "isatty", lambda: True)
    with patch("ro_claude_kit_cli.tui.run_tui") as run_tui_mock, \
         patch("ro_claude_kit_cli.main._is_code_project", return_value=False), \
         patch("ro_claude_kit_cli.main.console.print"), \
         patch("ro_claude_kit_cli.code_mode.run_unified_session") as run_unified:
        from ro_claude_kit_cli.main import _root
        ctx = MagicMock()
        ctx.invoked_subcommand = None
        _root(ctx)
    run_tui_mock.assert_called_once()
    run_unified.assert_not_called()


def test_ronin_tui_flag_launches_tui(monkeypatch, tmp_path) -> None:
    """`ronin --tui` opts into the full-screen TUI."""
    import sys as _sys
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_sys.stdout, "isatty", lambda: True)
    with patch("ro_claude_kit_cli.tui.run_tui") as run_tui_mock:
        from ro_claude_kit_cli.main import _root
        ctx = MagicMock()
        ctx.invoked_subcommand = None
        _root(ctx, tui=True)
    run_tui_mock.assert_called_once()


def test_bare_ronin_no_tui_opens_unified_session(monkeypatch, tmp_path) -> None:
    """`ronin --no-tui` keeps the classic stream-REPL behaviour."""
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
        _root(ctx, no_tui=True)
    assert "root" in calls
