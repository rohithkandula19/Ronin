from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ronin_cli import audio
from ronin_cli.main import app

runner = CliRunner()


def test_speak_aloud_builds_say_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "tts_engine", lambda: "say")
    captured = {}

    def fake_run(cmd, capture_output=True, text=True, timeout=120):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch.object(audio.subprocess, "run", fake_run):
        result = audio.speak("hello world", voice="Alex", rate=200)

    assert result is None  # spoke aloud, no file
    assert captured["cmd"] == ["say", "-v", "Alex", "-r", "200", "hello world"]


def test_speak_to_file_adds_output_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(audio, "tts_engine", lambda: "say")
    out = tmp_path / "clip.m4a"

    def fake_run(cmd, capture_output=True, text=True, timeout=120):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch.object(audio.subprocess, "run", fake_run):
        result = audio.speak("hi", out=out)
    assert result == out


def test_speak_no_engine_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "tts_engine", lambda: None)
    with pytest.raises(RuntimeError, match="no text-to-speech engine"):
        audio.speak("hi")


def test_speak_nonzero_exit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "tts_engine", lambda: "say")

    def fail_run(cmd, capture_output=True, text=True, timeout=120):
        return subprocess.CompletedProcess(cmd, 1, "", "bad voice")

    with patch.object(audio.subprocess, "run", fail_run):
        with pytest.raises(RuntimeError, match="text-to-speech failed"):
            audio.speak("hi")


def test_espeak_uses_wav_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(audio, "tts_engine", lambda: "espeak-ng")
    out = tmp_path / "v.wav"
    captured = {}

    def fake_run(cmd, capture_output=True, text=True, timeout=120):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch.object(audio.subprocess, "run", fake_run):
        audio.speak("hi", out=out, rate=150)
    assert captured["cmd"][0] == "espeak-ng"
    assert "-w" in captured["cmd"] and str(out) in captured["cmd"]
    assert "-s" in captured["cmd"]  # espeak rate flag


def test_cli_say_no_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("ronin_cli.audio.tts_engine", return_value=None):
        r = runner.invoke(app, ["util", "say", "hello"])
    assert r.exit_code == 2
    assert "text-to-speech" in r.stdout


def test_cli_say_speaks(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("ronin_cli.audio.tts_engine", return_value="say"), \
         patch("ronin_cli.audio.speak", return_value=None):
        r = runner.invoke(app, ["util", "say", "hello", "there"])
    assert r.exit_code == 0, r.stdout
    assert "spoke aloud" in r.stdout


def test_cli_say_saves_file(tmp_path: Path) -> None:
    out = tmp_path / "a.m4a"
    with patch("ronin_cli.audio.tts_engine", return_value="say"), \
         patch("ronin_cli.audio.speak", return_value=out):
        r = runner.invoke(app, ["util", "say", "hello", "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    assert "saved audio" in r.stdout


def test_cli_say_empty_text_errors() -> None:
    with patch("ronin_cli.audio.tts_engine", return_value="say"):
        r = runner.invoke(app, ["util", "say"])
    assert r.exit_code == 2
