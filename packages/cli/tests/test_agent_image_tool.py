from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ro_claude_kit_agent_patterns import FakeProvider, LLMResponse, ToolCall
from ro_claude_kit_cli import media
from ro_claude_kit_cli.code_mode import run_code_agent
from ro_claude_kit_cli.config import CSKConfig

PNG = b"\x89PNG\r\n\x1a\n" + b"img"


def test_image_tool_saves_into_project(tmp_path: Path) -> None:
    tool = media.build_image_tool(tmp_path)
    assert tool.name == "generate_image"
    with patch.object(media, "_image_bytes", return_value=(PNG, "image/png")):
        msg = tool.handler(prompt="a logo", path="assets/logo.png")
    saved = tmp_path / "assets" / "logo.png"
    assert saved.exists() and saved.read_bytes() == PNG
    assert "saved generated image to assets/logo.png" in msg


def test_image_tool_refuses_path_traversal(tmp_path: Path) -> None:
    tool = media.build_image_tool(tmp_path)
    with patch.object(media, "_image_bytes", return_value=(PNG, "image/png")):
        msg = tool.handler(prompt="x", path="../escape.png")
    assert msg.startswith("ERROR: refusing to write outside")
    assert not (tmp_path.parent / "escape.png").exists()


def test_image_tool_reports_backend_error(tmp_path: Path) -> None:
    tool = media.build_image_tool(tmp_path)
    with patch.object(media, "_image_bytes", side_effect=RuntimeError("net down")):
        msg = tool.handler(prompt="x", path="a.png")
    assert msg.startswith("ERROR: image generation failed") and "net down" in msg


def _image_using_provider() -> FakeProvider:
    return FakeProvider(responses=[
        LLMResponse(
            text="I'll generate the logo.",
            tool_calls=[ToolCall(id="t1", name="generate_image",
                                 arguments={"prompt": "panda logo", "path": "logo.png"})],
            stop_reason="tool_use", usage={"input_tokens": 10, "output_tokens": 5}),
        LLMResponse(text="Done — saved logo.png.", stop_reason="end_turn",
                    usage={"input_tokens": 5, "output_tokens": 3}),
    ])


def test_code_agent_can_generate_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = CSKConfig(provider="anthropic")
    with patch("ro_claude_kit_cli.code_mode.build_provider", return_value=_image_using_provider()), \
         patch.object(media, "_image_bytes", return_value=(PNG, "image/png")):
        # console=None → non-interactive; generate_image is gated but yolo auto-approves
        result = run_code_agent(config, "make a logo", root=tmp_path, console=None, yolo=True)
    assert result.success
    assert (tmp_path / "logo.png").exists()
