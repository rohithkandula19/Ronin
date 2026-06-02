from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from ronin_agent_patterns import FakeProvider, LLMResponse, ToolCall
from ronin_cli import media, runner
from ronin_cli.config import RoninConfig

PNG = b"\x89PNG\r\n\x1a\n" + b"img"


# ---------- build_media_tools (the conversational tools) ----------

def test_build_media_tools_exposes_three(tmp_path: Path) -> None:
    tools = media.build_media_tools([], root=tmp_path)
    assert {t.name for t in tools} == {"generate_image", "generate_video", "speak"}


def test_image_tool_records_artifact(tmp_path: Path) -> None:
    artifacts: list = []
    tools = {t.name: t for t in media.build_media_tools(artifacts, root=tmp_path)}
    with patch.object(media, "_image_bytes", return_value=(PNG, "image/png")):
        msg = tools["generate_image"].handler(prompt="a naruto", filename="naruto.png")
    assert (tmp_path / "naruto.png").exists()
    assert artifacts == [("image", tmp_path / "naruto.png")]
    assert "shown to the user" in msg


def test_video_tool_without_ffmpeg(tmp_path: Path) -> None:
    artifacts: list = []
    tools = {t.name: t for t in media.build_media_tools(artifacts, root=tmp_path)}
    with patch.object(media, "ffmpeg_available", return_value=False):
        msg = tools["generate_video"].handler(prompt="a city")
    assert msg.startswith("ERROR") and "ffmpeg" in msg
    assert artifacts == []


def test_speak_tool_no_engine(tmp_path: Path) -> None:
    tools = {t.name: t for t in media.build_media_tools([], root=tmp_path)}
    with patch("ronin_cli.audio.tts_engine", return_value=None):
        msg = tools["speak"].handler(text="hello")
    assert msg.startswith("ERROR")


def test_speak_tool_calls_engine(tmp_path: Path) -> None:
    # build the tools inside the patch — the handler binds audio.speak at build time
    with patch("ronin_cli.audio.tts_engine", return_value="say"), \
         patch("ronin_cli.audio.speak") as sp:
        tools = {t.name: t for t in media.build_media_tools([], root=tmp_path)}
        msg = tools["speak"].handler(text="hello there")
    sp.assert_called_once()
    assert "aloud" in msg


def test_show_artifacts_dispatches_and_clears(tmp_path: Path) -> None:
    img = tmp_path / "a.png"; img.write_bytes(PNG)
    vid = tmp_path / "v.mp4"; vid.write_bytes(b"mp4")
    artifacts = [("image", img), ("video", vid)]
    with patch.object(media, "display_image") as di, patch.object(media, "open_file") as of:
        media.show_artifacts(artifacts)
    di.assert_called_once()
    of.assert_called_once()
    assert artifacts == []  # cleared


# ---------- start_chat routes a plain-language request to the image tool ----------

def _queue_inputs(console: Console, lines: list[str]) -> None:
    it = iter(lines)
    console.input = lambda *a, **k: next(it)  # type: ignore[assignment]


def test_chat_routes_natural_language_to_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(provider="anthropic")

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    _queue_inputs(console, ["generate me a naruto image", ":q"])

    provider = FakeProvider(responses=[
        LLMResponse(text="Sure!", tool_calls=[ToolCall(id="t1", name="generate_image",
                    arguments={"prompt": "naruto, anime", "filename": "naruto.png"})],
                    stop_reason="tool_use", usage={"input_tokens": 10, "output_tokens": 4}),
        LLMResponse(text="Here's your Naruto image!", stop_reason="end_turn",
                    usage={"input_tokens": 5, "output_tokens": 5}),
    ])

    with patch("ronin_cli.runner.build_provider", return_value=provider), \
         patch.object(media, "_image_bytes", return_value=(PNG, "image/png")), \
         patch.object(media, "display_image") as display:
        runner.start_chat(config, console=console)

    # the agent called the image tool → file created and displayed
    assert (tmp_path / "naruto.png").exists()
    display.assert_called_once()
    assert "Naruto" in buf.getvalue()
