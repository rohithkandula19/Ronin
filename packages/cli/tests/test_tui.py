from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ro_claude_kit_cli.main import app


runner = CliRunner()


def test_tui_without_auth_errors(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = runner.invoke(app, ["tui"])
    assert r.exit_code == 2
    assert "ronin init" in r.stdout


def test_tui_imports_cleanly() -> None:
    """The textual app should at least import + construct without raising."""
    from ro_claude_kit_cli.tui import RoninApp
    from ro_claude_kit_cli.config import CSKConfig
    app_obj = RoninApp(config=CSKConfig(demo_mode=True))
    assert app_obj is not None


def test_tui_launches_run_tui(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When auth is present, the command should hand off to run_tui."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    runner.invoke(app, ["init", "--demo", "-y"])

    with patch("ro_claude_kit_cli.tui.run_tui") as mock_run:
        r = runner.invoke(app, ["tui"])
    assert r.exit_code == 0
    mock_run.assert_called_once()


@pytest.mark.asyncio
async def test_app_renders_core_widgets(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pilot test: app boots, key widgets exist, prompt is focused."""
    from ro_claude_kit_cli.config import CSKConfig
    from ro_claude_kit_cli.tui import RoninApp

    monkeypatch.chdir(tmp_path)
    csk_app = RoninApp(config=CSKConfig(demo_mode=True, provider="anthropic"))

    async with csk_app.run_test() as pilot:
        await pilot.pause()
        # Core layout pieces all present
        assert csk_app.query_one("#chat-pane")
        assert csk_app.query_one("#trace-pane")
        assert csk_app.query_one("#prompt")
        # Prompt is focused on mount
        from textual.widgets import Input
        assert csk_app.focused is csk_app.query_one("#prompt", Input)


@pytest.mark.asyncio
async def test_empty_submit_is_ignored(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Submitting an empty/whitespace prompt should not invoke the agent."""
    from ro_claude_kit_cli.config import CSKConfig
    from ro_claude_kit_cli.tui import RoninApp

    monkeypatch.chdir(tmp_path)
    csk_app = RoninApp(config=CSKConfig(demo_mode=True, provider="anthropic"))

    called: list[str] = []

    def fake_run(self, q: str) -> None:  # noqa: ARG001
        called.append(q)

    async with csk_app.run_test() as pilot:
        await pilot.pause()
        with patch.object(RoninApp, "_run_agent", fake_run):
            from textual.widgets import Input
            prompt = csk_app.query_one("#prompt", Input)
            # Empty submit
            prompt.post_message(Input.Submitted(prompt, "   ", validation_result=None))
            await pilot.pause()
    assert called == []


@pytest.mark.asyncio
async def test_action_clear_resets_history(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ro_claude_kit_cli.config import CSKConfig
    from ro_claude_kit_cli.tui import RoninApp

    monkeypatch.chdir(tmp_path)
    csk_app = RoninApp(config=CSKConfig(demo_mode=True, provider="anthropic"))
    csk_app.history.append(("user", "test"))

    async with csk_app.run_test() as pilot:
        await pilot.pause()
        csk_app.action_clear()
        await pilot.pause()
    assert csk_app.history == []
