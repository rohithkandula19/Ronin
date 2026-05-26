from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ro_claude_kit_cli import media
from ro_claude_kit_cli.main import app

runner = CliRunner()

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-data"


def test_pollinations_generates_and_saves(tmp_path: Path) -> None:
    captured = {}

    def fake_get(url, timeout=180.0):
        captured["url"] = url
        return PNG_BYTES, "image/png"

    out = tmp_path / "pic.png"
    with patch.object(media, "_http_get", fake_get):
        path = media.generate_image("a red panda", backend="pollinations", out=out, seed=7)

    assert path == out and out.read_bytes() == PNG_BYTES
    # prompt url-encoded into the pollinations endpoint, with our params
    assert "image.pollinations.ai/prompt/a%20red%20panda" in captured["url"]
    assert "seed=7" in captured["url"] and "nologo=true" in captured["url"]


def test_openai_backend_decodes_b64(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    b64 = base64.b64encode(PNG_BYTES).decode()

    def fake_post(url, body, headers, timeout=180.0):
        assert "images/generations" in url
        assert headers["Authorization"] == "Bearer sk-fake"
        return {"data": [{"b64_json": b64}]}

    out = tmp_path / "ai.png"
    with patch.object(media, "_http_post_json", fake_post):
        path = media.generate_image("a cat", backend="openai", out=out)
    assert path.read_bytes() == PNG_BYTES


def test_openai_without_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        media.generate_image("x", backend="openai", out=tmp_path / "x.png")


def test_unknown_backend_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown image backend"):
        media.generate_image("x", backend="nope", out=tmp_path / "x.png")


def test_ext_for_content_type() -> None:
    assert media._ext_for("image/png") == ".png"
    assert media._ext_for("image/jpeg") == ".jpg"
    assert media._ext_for("application/octet-stream") == ".png"


def test_display_falls_back_to_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    img = tmp_path / "p.png"
    img.write_bytes(PNG_BYTES)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    with patch.object(media.shutil, "which", return_value=None), \
         patch.object(media, "_open_in_viewer", return_value=True):
        assert media.display_image(img) == "open"


def test_cli_image_no_show(tmp_path: Path) -> None:
    fake_path = tmp_path / "ronin_image.png"
    fake_path.write_bytes(PNG_BYTES)
    with patch("ro_claude_kit_cli.media.generate_image", return_value=fake_path):
        r = runner.invoke(app, ["image", "a", "red", "panda", "--no-show", "--out", str(fake_path)])
    assert r.exit_code == 0, r.stdout
    assert "saved" in r.stdout


def test_cli_image_bad_size(tmp_path: Path) -> None:
    r = runner.invoke(app, ["image", "x", "--size", "huge", "--no-show"])
    assert r.exit_code == 2
    assert "--size" in r.stdout


def test_cli_image_backend_error_is_clean() -> None:
    with patch("ro_claude_kit_cli.media.generate_image", side_effect=RuntimeError("network down")):
        r = runner.invoke(app, ["image", "x", "--no-show"])
    assert r.exit_code == 1
    assert "failed" in r.stdout and "network down" in r.stdout
