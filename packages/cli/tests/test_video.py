from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ronin_cli import media
from ronin_cli.main import app

runner = CliRunner()

PNG = b"\x89PNG\r\n\x1a\n" + b"frame-bytes"


def test_generate_video_stitches_frames(tmp_path: Path) -> None:
    seeds_seen: list[int] = []

    def fake_image_bytes(prompt, backend, width, height, seed, model, api_key):
        seeds_seen.append(seed)
        return PNG, "image/png"

    def fake_ffmpeg(cmd, capture_output=True, text=True, timeout=180):
        # emulate ffmpeg writing the output file (last arg)
        Path(cmd[-1]).write_bytes(b"\x00\x00\x00 ftypisom fake-mp4")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    out = tmp_path / "clip.mp4"
    with patch.object(media, "ffmpeg_available", return_value=True), \
         patch.object(media, "_image_bytes", fake_image_bytes), \
         patch.object(media.subprocess, "run", fake_ffmpeg):
        result = media.generate_video("a panda", out=out, frames=4, fps=6, seed=100)

    assert result.path == out and out.exists()
    assert result.frames == 4 and result.fps == 6
    assert result.poster is not None and result.poster.exists()
    # each frame used an incrementing seed
    assert seeds_seen == [100, 101, 102, 103]


def test_generate_video_requires_ffmpeg(tmp_path: Path) -> None:
    with patch.object(media, "ffmpeg_available", return_value=False):
        with pytest.raises(RuntimeError, match="ffmpeg not found"):
            media.generate_video("x", out=tmp_path / "v.mp4", frames=3)


def test_generate_video_min_frames(tmp_path: Path) -> None:
    with patch.object(media, "ffmpeg_available", return_value=True):
        with pytest.raises(ValueError, match="at least 2 frames"):
            media.generate_video("x", out=tmp_path / "v.mp4", frames=1)


def test_generate_video_ffmpeg_failure_surfaces(tmp_path: Path) -> None:
    def fail_ffmpeg(cmd, capture_output=True, text=True, timeout=180):
        return subprocess.CompletedProcess(cmd, 1, "", "codec boom")

    with patch.object(media, "ffmpeg_available", return_value=True), \
         patch.object(media, "_image_bytes", return_value=(PNG, "image/png")), \
         patch.object(media.subprocess, "run", fail_ffmpeg):
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            media.generate_video("x", out=tmp_path / "v.mp4", frames=2)


def test_cli_video_without_ffmpeg_exits_2() -> None:
    with patch("ronin_cli.media.ffmpeg_available", return_value=False):
        r = runner.invoke(app, ["video", "a panda", "--no-show"])
    assert r.exit_code == 2
    assert "ffmpeg" in r.stdout


def test_cli_video_happy_path(tmp_path: Path) -> None:
    out = tmp_path / "v.mp4"
    fake = media.VideoResult(path=out, poster=None, frames=8, fps=8)
    out.write_bytes(b"mp4")
    with patch("ronin_cli.media.ffmpeg_available", return_value=True), \
         patch("ronin_cli.media.generate_video", return_value=fake):
        r = runner.invoke(app, ["video", "a panda", "--no-show", "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    assert "saved" in r.stdout and "8 frames" in r.stdout
