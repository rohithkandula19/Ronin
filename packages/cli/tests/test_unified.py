from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from ro_claude_kit_agent_patterns import FakeProvider, LLMResponse, ToolCall
from ro_claude_kit_cli import code_mode, media
from ro_claude_kit_cli.config import CSKConfig

PNG = b"\x89PNG\r\n\x1a\n" + b"img"


def _queue(console: Console, lines: list[str]) -> None:
    it = iter(lines)
    console.input = lambda *a, **k: next(it)  # type: ignore[assignment]


def test_unified_has_all_tool_types(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One agent gets code + media + data tools together."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = CSKConfig(provider="anthropic", demo_mode=True)  # demo → data tools exist

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    _queue(console, ["hi", "/q"])
    provider = FakeProvider(responses=[LLMResponse(text="hello!", stop_reason="end_turn", usage={})])

    with patch("ro_claude_kit_cli.code_mode.build_provider", return_value=provider):
        code_mode.run_unified_session(config, root=tmp_path, console=console)

    names = set(provider.calls[0]["tool_names"])
    assert {"read_file", "write_file", "run_command"} <= names          # code
    assert {"generate_image", "generate_video", "speak"} <= names        # media
    assert any(n.startswith("stripe_") for n in names)                   # data (demo)
    # no duplicate generate_image (code's build_image_tool suppressed)
    assert sum(1 for n in provider.calls[0]["tool_names"] if n == "generate_image") == 1


def test_unified_generates_image_from_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = CSKConfig(provider="anthropic", demo_mode=True)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    _queue(console, ["generate a panda image", "/q"])
    provider = FakeProvider(responses=[
        LLMResponse(text="On it.", tool_calls=[ToolCall(id="t1", name="generate_image",
                    arguments={"prompt": "a panda", "filename": "panda.png"})],
                    stop_reason="tool_use", usage={}),
        LLMResponse(text="Here's your panda!", stop_reason="end_turn", usage={}),
    ])

    with patch("ro_claude_kit_cli.code_mode.build_provider", return_value=provider), \
         patch.object(media, "_image_bytes", return_value=(PNG, "image/png")), \
         patch.object(media, "display_image") as disp:
        code_mode.run_unified_session(config, root=tmp_path, console=console)

    assert (tmp_path / "panda.png").exists()
    disp.assert_called_once()


def test_unified_can_write_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = CSKConfig(provider="anthropic", demo_mode=True)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    _queue(console, ["write panda.py", "/q"])
    provider = FakeProvider(responses=[
        LLMResponse(text="Writing it.", tool_calls=[ToolCall(id="t1", name="write_file",
                    arguments={"path": "panda.py", "content": "print('panda')\n"})],
                    stop_reason="tool_use", usage={}),
        LLMResponse(text="Done.", stop_reason="end_turn", usage={}),
    ])

    with patch("ro_claude_kit_cli.code_mode.build_provider", return_value=provider):
        # yolo=False but no console gate interaction needed since write is approved via yolo
        code_mode.run_unified_session(config, root=tmp_path, console=console, yolo=True)

    assert (tmp_path / "panda.py").read_text() == "print('panda')\n"


def test_verify_runs_a_second_self_check_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With /verify on, a turn that uses a tool triggers a second verification pass."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = CSKConfig(provider="anthropic", verify=True)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    _queue(console, ["create note.txt with hi", "/q"])
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="t1", name="write_file",
                    arguments={"path": "note.txt", "content": "hi"})],
                    stop_reason="tool_use", usage={}),
        LLMResponse(text="created note.txt", stop_reason="end_turn", usage={}),
        LLMResponse(text="Verified.", stop_reason="end_turn", usage={}),  # the verify pass
    ])
    with patch("ro_claude_kit_cli.code_mode.build_provider", return_value=provider):
        code_mode.run_unified_session(config, root=tmp_path, console=console, yolo=True)

    assert len(provider.calls) >= 3            # main turn (2 calls) + verify pass
    assert "self-verifying" in buf.getvalue()
    assert (tmp_path / "note.txt").exists()
