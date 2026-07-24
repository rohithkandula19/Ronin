from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ronin_cli import media
from ronin_cli.main import app

runner = CliRunner()
MP4 = b"\x00\x00\x00 ftypisom real-motion-mp4"


def test_replicate_create_poll_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_fake")
    out = tmp_path / "clip.mp4"
    statuses: list[str] = []

    create_resp = {"id": "pred1", "status": "starting",
                   "urls": {"get": "https://api.replicate.com/v1/predictions/pred1"}}
    poll_seq = [
        {"status": "processing"},
        {"status": "succeeded", "output": "https://replicate.delivery/out.mp4"},
    ]

    def fake_post(url, body, headers, timeout=180.0):
        assert "models/minimax/video-01/predictions" in url
        assert headers["Authorization"] == "Bearer r8_fake"
        assert body["input"]["prompt"] == "a panda surfing"
        return create_resp

    def fake_get_json(url, headers, timeout=60.0):
        return poll_seq.pop(0)

    def fake_get(url, timeout=180.0):
        assert url == "https://replicate.delivery/out.mp4"
        return MP4, "video/mp4"

    with patch.object(media, "_http_post_json", fake_post), \
         patch.object(media, "_http_get_json", fake_get_json), \
         patch.object(media, "_http_get", fake_get), \
         patch.object(media.time, "sleep", lambda s: None):
        result = media.generate_video_replicate("a panda surfing", out=out,
                                                on_status=statuses.append)

    assert result.path == out and out.read_bytes() == MP4
    assert "succeeded" in statuses


def test_replicate_requires_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="REPLICATE_API_TOKEN"):
        media.generate_video_replicate("x", out=tmp_path / "v.mp4")


def test_replicate_failure_surfaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_fake")
    with patch.object(media, "_http_post_json", return_value={"id": "p", "status": "starting",
                      "urls": {"get": "u"}}), \
         patch.object(media, "_http_get_json", return_value={"status": "failed", "error": "nsfw"}), \
         patch.object(media.time, "sleep", lambda s: None):
        with pytest.raises(RuntimeError, match="failed: nsfw"):
            media.generate_video_replicate("x", out=tmp_path / "v.mp4")


def test_replicate_list_output_takes_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_fake")
    out = tmp_path / "v.mp4"
    with patch.object(media, "_http_post_json", return_value={"id": "p", "status": "succeeded",
                      "urls": {"get": "u"}, "output": ["a.mp4", "b.mp4"]}), \
         patch.object(media, "_http_get_json", return_value={"status": "succeeded", "output": ["a.mp4", "b.mp4"]}), \
         patch.object(media, "_http_get", return_value=(MP4, "video/mp4")) as g, \
         patch.object(media.time, "sleep", lambda s: None):
        media.generate_video_replicate("x", out=out)
    g.assert_called_once()
    assert g.call_args[0][0] == "b.mp4"  # last url in the list


def test_cli_video_replicate_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    r = runner.invoke(app, ["util", "video", "a panda", "--engine", "replicate", "--no-show"])
    assert r.exit_code == 1
    assert "REPLICATE_API_TOKEN" in r.stdout


def test_cli_video_unknown_engine() -> None:
    r = runner.invoke(app, ["util", "video", "x", "--engine", "bogus", "--no-show"])
    assert r.exit_code == 2
    assert "unknown --engine" in r.stdout
