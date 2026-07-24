"""Headless pilot tests for the rebuilt ronin TUI.

These run in CI with no real TTY: they use Textual's ``App.run_test()`` to assert
the app mounts, the dashboard widgets compose, submitting text does not crash, and
the approval modal opens and closes. They never touch the network (the agent call
is patched out) and never read the real on-disk config (auth tests are config
isolated)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ronin_cli.main import app

runner = CliRunner()


# --------------------------------------------------------------------------- #
# command-level dispatch (config-isolated so they never hang on a real config)
# --------------------------------------------------------------------------- #

def test_tui_without_auth_errors(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Isolate config dirs so the runner can't read a developer's real ~/.ronin
    # (an authed config there would send `ronin tui` into a blocking run_tui()).
    from ronin_cli import config as cfgmod
    monkeypatch.setattr(cfgmod, "PROJECT_DIR", tmp_path / ".ronin")
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / "user")
    r = runner.invoke(app, ["util", "tui"])
    assert r.exit_code == 2
    assert "ronin init" in r.stdout


def test_tui_imports_cleanly() -> None:
    """The textual app should at least import + construct without raising."""
    from ronin_cli.config import RoninConfig
    from ronin_cli.tui import RoninApp
    app_obj = RoninApp(config=RoninConfig(demo_mode=True))
    assert app_obj is not None


def test_tui_launches_run_tui(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When auth is present, the command should hand off to run_tui."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    runner.invoke(app, ["init", "--demo", "-y"])

    with patch("ronin_cli.tui.run_tui") as mock_run:
        r = runner.invoke(app, ["util", "tui"])
    assert r.exit_code == 0
    mock_run.assert_called_once()


# --------------------------------------------------------------------------- #
# pilot tests against the live (headless) Textual app
# --------------------------------------------------------------------------- #

def _demo_app():
    from ronin_cli.config import RoninConfig
    from ronin_cli.tui import RoninApp
    return RoninApp(config=RoninConfig(demo_mode=True, provider="cerebras", model="zai-glm-4.7"))


@pytest.mark.asyncio
async def test_app_mounts_and_composes_core_widgets(tmp_path, monkeypatch) -> None:
    """App boots and every dashboard region composes: header, sidebar,
    conversation, live trace, input, footer."""
    monkeypatch.chdir(tmp_path)
    csk_app = _demo_app()
    async with csk_app.run_test() as pilot:
        await pilot.pause()
        for sel in (
            "#topbar", "#brand", "#stats",       # header + live stats
            "#sidebar",                          # left sidebar
            "#chat-pane", "#chat-scroll",        # conversation
            "#trace-pane", "#trace-scroll",      # live trace
            "#input-row", "#prompt",             # pinned input
            "#hintbar", "#hints", "#mode-chip",  # footer hints + mode chip
        ):
            assert csk_app.query(sel), f"missing widget {sel}"
        from textual.widgets import Footer
        assert csk_app.query(Footer)
    assert csk_app.return_code in (None, 0)


@pytest.mark.asyncio
async def test_prompt_is_focused_on_mount(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    csk_app = _demo_app()
    async with csk_app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input
        assert csk_app.focused is csk_app.query_one("#prompt", Input)


@pytest.mark.asyncio
async def test_submitting_text_does_not_crash(tmp_path, monkeypatch) -> None:
    """Submitting a real prompt drives the agent path without blowing up the app
    (the agent worker is stubbed so no network is touched)."""
    monkeypatch.chdir(tmp_path)
    from ronin_cli.tui import RoninApp
    csk_app = _demo_app()
    seen: list[str] = []
    with patch.object(RoninApp, "_run_agent", lambda self, q: seen.append(q)):
        async with csk_app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import Input
            prompt = csk_app.query_one("#prompt", Input)
            prompt.post_message(Input.Submitted(prompt, "add a retry helper", validation_result=None))
            await pilot.pause()
    assert seen == ["add a retry helper"]
    assert csk_app.history[-1] == ("user", "add a retry helper")


@pytest.mark.asyncio
async def test_empty_submit_is_ignored(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from ronin_cli.tui import RoninApp
    csk_app = _demo_app()
    called: list[str] = []
    async with csk_app.run_test() as pilot:
        await pilot.pause()
        with patch.object(RoninApp, "_run_agent", lambda self, q: called.append(q)):
            from textual.widgets import Input
            prompt = csk_app.query_one("#prompt", Input)
            prompt.post_message(Input.Submitted(prompt, "   ", validation_result=None))
            await pilot.pause()
    assert called == []


@pytest.mark.asyncio
async def test_approval_modal_opens_and_closes(tmp_path, monkeypatch) -> None:
    """The approval modal pushes onto the stack and dismisses with the chosen
    answer (approve -> True, deny -> False)."""
    monkeypatch.chdir(tmp_path)
    from ronin_cli.tui import ApprovalScreen
    csk_app = _demo_app()
    async with csk_app.run_test() as pilot:
        await pilot.pause()
        answers: list = []
        csk_app.push_screen(
            ApprovalScreen("write_file", {"path": "out.txt", "content": "hi"}),
            answers.append,
        )
        await pilot.pause()
        assert isinstance(csk_app.screen, ApprovalScreen)
        # deny then re-open and approve
        csk_app.screen.action_deny()
        await pilot.pause()
        assert answers == [False]
        assert not isinstance(csk_app.screen, ApprovalScreen)  # modal closed

        csk_app.push_screen(ApprovalScreen("write_file", {"path": "out.txt"}), answers.append)
        await pilot.pause()
        csk_app.screen.action_approve()
        await pilot.pause()
        assert answers == [False, True]


@pytest.mark.asyncio
async def test_shift_tab_cycles_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    csk_app = _demo_app()
    async with csk_app.run_test() as pilot:
        await pilot.pause()
        start = csk_app.INPUT_MODES[csk_app.mode_index]
        csk_app.action_cycle_mode()
        await pilot.pause()
        assert csk_app.INPUT_MODES[csk_app.mode_index] != start


@pytest.mark.asyncio
async def test_queue_while_busy(tmp_path, monkeypatch) -> None:
    """Typing while the agent is busy queues the message instead of starting a
    second turn."""
    monkeypatch.chdir(tmp_path)
    from ronin_cli.tui import RoninApp
    csk_app = _demo_app()
    with patch.object(RoninApp, "_run_agent", lambda self, q: None):
        async with csk_app.run_test() as pilot:
            await pilot.pause()
            csk_app.busy = True
            from textual.widgets import Input
            prompt = csk_app.query_one("#prompt", Input)
            prompt.post_message(Input.Submitted(prompt, "later please", validation_result=None))
            await pilot.pause()
    assert csk_app.queue == ["later please"]


@pytest.mark.asyncio
async def test_stats_update_reflects_in_header(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    csk_app = _demo_app()
    async with csk_app.run_test() as pilot:
        await pilot.pause()
        csk_app.tokens_up += 1200
        csk_app.tokens_down += 800
        csk_app.last_latency = 0.9
        await pilot.pause()
        label = csk_app._stats_label()
    assert "1200" in label and "800" in label and "0.9s" in label


@pytest.mark.asyncio
async def test_action_clear_resets_history(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    csk_app = _demo_app()
    csk_app.history.append(("user", "test"))
    async with csk_app.run_test() as pilot:
        await pilot.pause()
        csk_app.action_clear()
        await pilot.pause()
    assert csk_app.history == []


@pytest.mark.asyncio
async def test_slash_quit_exits(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    csk_app = _demo_app()
    async with csk_app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input
        prompt = csk_app.query_one("#prompt", Input)
        prompt.post_message(Input.Submitted(prompt, "/q", validation_result=None))
        await pilot.pause()
    assert csk_app.return_code == 0
