from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from ronin_cli import vision
from ronin_cli.config import RoninConfig
from ronin_cli.main import app

runner = CliRunner()
PNG = b"\x89PNG\r\n\x1a\n" + b"img-bytes"


def test_media_type_detection(tmp_path: Path) -> None:
    assert vision._media_type(Path("a.png")) == "image/png"
    assert vision._media_type(Path("a.JPG")) == "image/jpeg"
    assert vision._media_type(Path("a.webp")) == "image/webp"
    assert vision._media_type(Path("a.unknown")) == "image/png"


def test_describe_missing_file(tmp_path: Path) -> None:
    cfg = RoninConfig(provider="anthropic", anthropic_api_key="k")
    with pytest.raises(FileNotFoundError):
        vision.describe_image(cfg, tmp_path / "nope.png")


def test_anthropic_vision_sends_image_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    img = tmp_path / "pic.png"; img.write_bytes(PNG)
    cfg = RoninConfig(provider="anthropic", anthropic_api_key="k", model="claude-sonnet-4-6")

    captured = {}
    fake_block = MagicMock(); fake_block.type = "text"; fake_block.text = "A red panda."
    fake_resp = MagicMock(); fake_resp.content = [fake_block]
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = lambda **kw: captured.update(kw) or fake_resp

    with patch("anthropic.Anthropic", return_value=fake_client):
        answer = vision.describe_image(cfg, img, "what is this?")

    assert answer == "A red panda."
    content = captured["messages"][0]["content"]
    kinds = [b["type"] for b in content]
    assert "image" in kinds and "text" in kinds
    img_block = next(b for b in content if b["type"] == "image")
    assert img_block["source"]["media_type"] == "image/png"
    assert img_block["source"]["type"] == "base64"


def test_openai_vision_uses_image_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    img = tmp_path / "pic.jpg"; img.write_bytes(PNG)
    cfg = RoninConfig(provider="openai", openai_api_key="sk-x", model="gpt-4o")

    payload = {"choices": [{"message": {"content": "A cat."}}]}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(payload).encode()

    seen = {}

    def fake_urlopen(req, timeout=120):
        seen["body"] = json.loads(req.data)
        return _Resp()

    with patch("urllib.request.urlopen", fake_urlopen):
        answer = vision.describe_image(cfg, img, "what is this?")
    assert answer == "A cat."
    content = seen["body"]["messages"][0]["content"]
    url_block = next(b for b in content if b["type"] == "image_url")
    assert url_block["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_openai_vision_requires_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    img = tmp_path / "p.png"; img.write_bytes(PNG)
    cfg = RoninConfig(provider="groq", model="x")
    with pytest.raises(RuntimeError, match="API key"):
        vision.describe_image(cfg, img)


def test_cli_see_without_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    img = tmp_path / "p.png"; img.write_bytes(PNG)
    r = runner.invoke(app, ["util", "see", str(img)])
    assert r.exit_code == 2
    assert "vision needs a provider key" in r.stdout


def test_cli_see_happy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    img = tmp_path / "p.png"; img.write_bytes(PNG)
    with patch("ronin_cli.vision.describe_image", return_value="A red panda samurai."), \
         patch("ronin_cli.media.display_image", return_value="none"):
        r = runner.invoke(app, ["util", "see", str(img), "what", "is", "this?"])
    assert r.exit_code == 0, r.stdout
    assert "red panda samurai" in r.stdout
